"""The external controller: three levers learned from per-repo session traces.

  Lever 1  micro-skills   miner proposes facts + a confirm regex; the controller (not the model) checks each
                          regex against every session's shell commands; >= MIN_CONFIRM distinct sessions -> skills.md
  Lever 2  tool pruning   prunable tools loaded but uncalled in the last N_PRUNE sessions -> removed via allowlist.json
  Lever 3  loop guard     every K_GUARD steps, a rule-based trigger; if open, a cheap judge may inject one redirect

State lives in <home>/<repo-hash>/ : sessions/*.jsonl, candidates.json, skills.md, allowlist.json, settings.json
"""
import hashlib
import json
import os
import re
import time

from . import agent as agentmod
from . import toyrepo
from .tools import ALL_TOOLS, PROTECTED

MIN_CONFIRM = 3
CONTRA_WEIGHT = 5  # paper's lambda: one contradiction outweighs several confirmations
N_PRUNE = 4
K_GUARD = 3
GUARD_WINDOW = 6
GUARD_CONFIDENCE = 0.7
MAX_REDIRECTS = 1
LEVERS = ("skills", "prune", "guard")
ARMS = {"baseline": (), "controlled": LEVERS,
        "skills-only": ("skills",), "prune-only": ("prune",), "guard-only": ("guard",)}

CONTRA_MARKERS = re.compile(r"command not found|No module named|No rule to make target|can't open file")
TESTS_RAN = re.compile(r"\d+ (passed|failed|errors?)\b")


def default_home():
    return os.environ.get("ECON_HOME") or os.path.join(os.path.expanduser("~"), ".econ")


def repo_hash(workdir):
    return hashlib.sha1(os.path.realpath(workdir).encode()).hexdigest()[:12]


def parse_json_obj(text):
    """First JSON object in a model reply, or None."""
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return None


# ---------------------------------------------------------------- trace digests

def session_commands(events):
    """Shell commands of one session with whether each one held or was contradicted."""
    cmds = []
    for e in events:
        if e.get("type") != "step" or e.get("tool") != "run_shell":
            continue
        out = e.get("output", "")
        m = re.match(r"exit=(-?\d+)", out)
        rc = int(m.group(1)) if m else 1
        contradicted = bool(CONTRA_MARKERS.search(out))
        held = not contradicted and (rc == 0 or bool(TESTS_RAN.search(out)))
        cmds.append({"cmd": str(e.get("args", {}).get("command", "")), "rc": rc,
                     "held": held, "contradicted": contradicted})
    return cmds


def signature(output):
    """Normalized first error line: digits and paths stripped, so the same failure matches itself."""
    for line in output.splitlines():
        if re.search(r"error|Error|FAILED|failed|not found", line) and not line.startswith("exit="):
            line = re.sub(r"(/[^\s:'\"]+)+", "<path>", line)
            return re.sub(r"\d+", "N", line).strip()[:160]
    return ""


# ---------------------------------------------------------------- lever 1: micro-skills

def evaluate_candidate(entry, sid, commands):
    """Record whether session `sid` confirmed or contradicted this candidate (each session counts once)."""
    rx = re.compile(entry["confirm"])
    matched = [c for c in commands if rx.search(c["cmd"])]
    if any(c["contradicted"] for c in matched):
        if sid not in entry["contradictions"]:
            entry["contradictions"].append(sid)
    elif any(c["held"] for c in matched):
        if sid not in entry["confirmations"]:
            entry["confirmations"].append(sid)


def update_status(entry, sid):
    support = len(entry["confirmations"]) - CONTRA_WEIGHT * len(entry["contradictions"])
    if entry["status"] == "candidate" and len(entry["confirmations"]) >= MIN_CONFIRM and support >= 0:
        entry["status"] = "promoted"
        entry["promoted_after_session"] = sid
    elif entry["status"] == "promoted" and support < 0:
        entry["status"] = "retired"  # permanent; the miner may propose a replacement under a new id
        entry["retired_after_session"] = sid


def admit_proposals(ledger, proposals, sid, commands, history):
    """Validate miner output and merge it into the ledger.

    history: {sid: commands} for earlier sessions, so a new candidate is checked retroactively.
    A proposal is admitted only if its confirm regex compiles and matches a command that held this session.
    """
    admitted = []
    for p in proposals[:4]:
        if not isinstance(p, dict):
            continue
        fid = re.sub(r"[^a-z0-9-]", "-", str(p.get("id", "")).lower()).strip("-")[:48]
        fact, confirm = str(p.get("fact", "")).strip(), str(p.get("confirm", ""))
        if not fid or not fact or not confirm or len(confirm) > 200:
            continue
        try:
            rx = re.compile(confirm)
        except re.error:
            continue
        if not any(c["held"] and rx.search(c["cmd"]) for c in commands):
            continue  # ungrounded: no evidence in this session's trace
        if fid in ledger:
            continue  # existing ids keep their fact and detector
        entry = {"fact": fact, "confirm": confirm, "proposed_in": sid, "confirmations": [],
                 "contradictions": [], "status": "candidate"}
        for old_sid in sorted(history):
            evaluate_candidate(entry, old_sid, history[old_sid])
        ledger[fid] = entry
        admitted.append(fid)
    return admitted


def record_session(ledger, sid, commands):
    for entry in ledger.values():
        if entry["status"] != "retired":
            evaluate_candidate(entry, sid, commands)
            update_status(entry, sid)


def render_manual(ledger):
    facts = [e["fact"] for e in ledger.values() if e["status"] == "promoted"]
    if not facts:
        return ""
    return "# repository manual, learned from prior sessions in this repository\n" + \
        "".join("- %s\n" % f for f in facts)


MINER_SYSTEM = "You extract durable operational facts about a software repository from agent traces. Reply with JSON only."

MINER_PROMPT = """Below is the trace digest of ONE finished coding-agent session in a repository.
Propose durable, repository-specific facts that a FUTURE task in this repository would want stated up front
to avoid wasted commands: the working test command, required environment variables, setup steps that must run
first (e.g. generating fixtures), which formatter to use, entry points. Do NOT emit task-specific fixes.

Existing fact ids (if you see the same fact again, reuse its id exactly):
{existing}

Session digest (one line per tool call):
{digest}

Return ONLY a JSON object: {{"facts": [{{"id": "short-kebab-id", "fact": "one declarative sentence for the agent's prompt",
"confirm": "Python regex that matches the shell command line that shows the fact holding, e.g. PYTHONPATH=src"}}]}}
At most 4 facts. Only facts the digest supports."""


def digest_steps(events, limit=24):
    lines = []
    for e in events:
        if e.get("type") == "step":
            arg = e["args"].get("command") or e["args"].get("path") or e["args"].get("pattern") or ""
            out = e.get("output", "")
            first = out.splitlines()[0] if out else ""
            sig = signature(out)
            lines.append("%d %s %s -> %s%s" % (e["step"], e["tool"], str(arg)[:120], first[:40],
                                              (" | " + sig) if sig else ""))
    return "\n".join(lines[-limit:])


# ---------------------------------------------------------------- lever 2: tool pruning

def compute_pruning(records, n=N_PRUNE, protected=PROTECTED, already=()):
    """records: chronological [(sid, loaded_tools, used_tools)] since the last restore.

    Returns {tool: [sids that justify it]} for tools loaded-but-uncalled in each of the last n sessions."""
    if len(records) < n:
        return {}
    window = records[-n:]
    out = {}
    for tool in ALL_TOOLS:
        if tool in protected or tool in already:
            continue
        if all(tool in loaded and tool not in used for _, loaded, used in window):
            out[tool] = [sid for sid, _, _ in window]
    return out


# ---------------------------------------------------------------- lever 3: loop guard

JUDGE_SYSTEM = "You monitor an AI coding agent for unproductive loops. Reply with JSON only."

JUDGE_PROMPT = """A rule-based trigger flagged this agent's recent steps as a possible unproductive loop ({trigger}).
Judge whether it is STUCK (repeating the same kind of command, edit, or failure with no progress) or still
making progress (failures changing, new hypotheses). Favor stuck=false when there is real progress.
If stuck, write ONE short sentence addressed to the agent that names the repeated pattern and proposes a
materially different next step (e.g. read the error, inspect the Makefile or scripts/, check how tests import
the package). Do not tell it to stop or give up, and do not write the code fix.

Recent steps (oldest to newest):
{digest}

Return ONLY JSON: {{"stuck": true|false, "confidence": 0.0-1.0, "redirect": "<one sentence or empty>"}}"""


def rule_trigger(steps, window=GUARD_WINDOW):
    """Cheap, model-free check. Like the paper, repeat counters run over the whole trajectory so far;
    failure density looks at the recent window. Returns every firing reason joined by '; ', or ''
    (all reasons are kept so the judge sees each running counter, not just the first one that fired)."""
    reasons = []
    failed = [s for s in steps if not s["ok"]]
    sigs = [x for x in (signature(s["output"]) for s in failed) if x]
    if len(sigs) != len(set(sigs)):
        reasons.append("repeated failure signature (%d failing outputs, %d distinct)" % (len(sigs), len(set(sigs))))
    acts = [json.dumps([s["tool"], s["args"]], sort_keys=True) for s in failed]
    if len(acts) != len(set(acts)):
        reasons.append("the same failing action was repeated (most repeated: %dx)" % max(acts.count(a) for a in acts))
    writes = [s["args"].get("path") for s in steps if s["tool"] in ("write_file", "replace_in_file")]
    if writes and max(writes.count(w) for w in writes) >= 3:
        reasons.append("rewriting the same file")
    recent = [s for s in steps[-window:] if not s["ok"]]
    if len(recent) >= 2:
        reasons.append("%d failed steps in the last %d" % (len(recent), len(steps[-window:])))
    return "; ".join(reasons)


class LoopGuard(object):
    def __init__(self, backend, log, k=K_GUARD, max_steps=None):
        self.backend, self.log, self.k, self.max_steps = backend, log, k, max_steps
        self.redirects = 0
        self.tokens = 0

    def __call__(self, step, history):
        if step % self.k or self.redirects >= MAX_REDIRECTS:
            return None
        if self.max_steps and step >= self.max_steps:
            return None  # no step follows, so a judge call here could never redirect anything
        steps = [h for h in history if h["type"] == "step"]
        trigger = rule_trigger(steps)
        if not trigger:
            self.log({"type": "guard", "step": step, "trigger": "", "judged": False})
            return None
        reply = self.backend.complete(JUDGE_SYSTEM, JUDGE_PROMPT.format(trigger=trigger, digest=digest_steps(steps, limit=10)),
                                      role="small")
        self.tokens += reply.tokens
        self.log({"type": "llm", "who": "guard", "step": step, "usage": reply.usage, "tokens": reply.tokens})
        v = parse_json_obj(reply.text) or {}
        redirect = " ".join(str(v.get("redirect", "")).split())[:300]
        try:
            conf = float(v.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        act = bool(v.get("stuck")) and conf >= GUARD_CONFIDENCE and bool(redirect)
        self.log({"type": "guard", "step": step, "trigger": trigger, "judged": True, "verdict": v, "redirect": act})
        if act:
            self.redirects += 1
            return "Loop guard: " + redirect
        return None


# ---------------------------------------------------------------- state + session runner

class State(object):
    def __init__(self, home, workdir):
        self.dir = os.path.join(home, repo_hash(workdir))
        self.sessions_dir = os.path.join(self.dir, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def load(self, name, default):
        try:
            with open(self.path(name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def save(self, name, obj):
        tmp = self.path(name) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2)
        os.replace(tmp, self.path(name))

    def session_files(self):
        return sorted(os.path.join(self.sessions_dir, f) for f in os.listdir(self.sessions_dir) if f.endswith(".jsonl"))

    @staticmethod
    def read_events(path):
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def all_sessions(self):
        """{sid: events}"""
        out = {}
        for p in self.session_files():
            ev = self.read_events(p)
            if ev and ev[0].get("type") == "start":
                out[ev[0]["session"]] = ev
        return out

    def ledger(self):
        return self.load("candidates.json", {})

    def allowlist(self):
        return self.load("allowlist.json", {"tools": list(ALL_TOOLS), "pruned": {}, "restored_after": 0,
                                             "restores": []})

    def write_manual(self, ledger):
        with open(self.path("skills.md"), "w") as f:
            f.write(render_manual(ledger))

    def manual(self):
        try:
            with open(self.path("skills.md")) as f:
                return f.read()
        except OSError:
            return ""

    def tool_records(self, after_sid):
        recs = []
        for sid, ev in sorted(self.all_sessions().items()):
            if sid <= after_sid:
                continue
            used = {e["tool"] for e in ev if e.get("type") == "step"}
            recs.append((sid, set(ev[0]["tools"]), used))
        return recs

    def update_pruning(self, sid):
        allow = self.allowlist()
        new = compute_pruning(self.tool_records(allow["restored_after"]), already=allow["pruned"])
        for tool, sids in new.items():
            allow["pruned"][tool] = sids
        allow["tools"] = [t for t in ALL_TOOLS if t not in allow["pruned"]]
        self.save("allowlist.json", allow)
        return new

    def restore_tools(self):
        allow = self.allowlist()
        sessions = self.all_sessions()
        allow["restores"].append({"after_session": max(sessions) if sessions else 0,
                                  "restored": sorted(allow["pruned"])})
        allow["restored_after"] = max(sessions) if sessions else 0  # unused streaks restart from here
        allow["pruned"] = {}
        allow["tools"] = list(ALL_TOOLS)
        self.save("allowlist.json", allow)
        return allow["restores"][-1]["restored"]

    def learn_skills(self, sid, events, backend, log):
        ledger = self.ledger()
        commands = session_commands(events)
        history = {s: session_commands(ev) for s, ev in self.all_sessions().items() if s != sid}
        existing = "\n".join("%s: %s" % (k, v["fact"]) for k, v in ledger.items()) or "(none)"
        reply = backend.complete(MINER_SYSTEM, MINER_PROMPT.format(existing=existing, digest=digest_steps(events)),
                                 role="small")
        log({"type": "llm", "who": "miner", "usage": reply.usage, "tokens": reply.tokens})
        obj = parse_json_obj(reply.text) or {}
        facts = obj.get("facts") if isinstance(obj.get("facts"), list) else []
        admitted = admit_proposals(ledger, facts, sid, commands, history)
        record_session(ledger, sid, commands)
        log({"type": "miner", "proposed": len(facts), "admitted": admitted})
        self.save("candidates.json", ledger)
        self.write_manual(ledger)
        return reply.tokens


def run_session(task_id, workdir, home, backend, levers=LEVERS, max_steps=12, arm=None):
    """One task = one agent session wrapped by the controller with the given levers on."""
    toyrepo.seed(workdir, task_id)
    state = State(home, workdir)
    sid = max(state.all_sessions() or [0]) + 1
    settings = {"tools": state.allowlist()["tools"] if "prune" in levers else list(ALL_TOOLS),
                "manual": state.manual() if "skills" in levers else ""}
    # The agent is configured only through its settings file, like a real harness.
    with open(state.path("settings.json"), "w") as f:
        json.dump(settings, f, indent=2)
    settings = agentmod.load_settings(state.path("settings.json"))
    events = []
    log_path = os.path.join(state.sessions_dir, "%04d-task%02d.jsonl" % (sid, task_id))
    logf = open(log_path, "w")

    def log(e):
        e = dict(e, t=round(time.time(), 2))
        events.append(e)
        logf.write(json.dumps(e) + "\n")
        logf.flush()

    log({"type": "start", "session": sid, "task": task_id, "arm": arm, "levers": list(levers),
         "tools": settings["tools"], "skills": [k for k, v in state.ledger().items() if v["status"] == "promoted"]
         if "skills" in levers else []})
    try:
        guard = LoopGuard(backend, log, max_steps=max_steps) if "guard" in levers else None
        res = agentmod.run_agent(toyrepo.task_prompt(task_id), workdir, settings, backend,
                                 on_step=guard, log=log, max_steps=max_steps)
        passed, tail = toyrepo.check(workdir, task_id)
        ctl = guard.tokens if guard else 0
        if "skills" in levers:
            ctl += state.learn_skills(sid, events, backend, log)
        pruned = state.update_pruning(sid) if "prune" in levers else {}
        end = {"type": "end", "session": sid, "task": task_id, "passed": passed, "finished": res["finished"],
               "steps": res["steps"], "agent_tokens": res["tokens"], "controller_tokens": ctl,
               "total_tokens": res["tokens"] + ctl, "newly_pruned": sorted(pruned),
               # cache-aware notional price reported by the CLI (0 for backends that do not report it)
               "cost_usd": round(sum(e["usage"].get("cost_usd", 0) for e in events if e["type"] == "llm"), 6),
               "redirects": guard.redirects if guard else 0, "check_tail": tail[-300:]}
        log(end)
    finally:
        logf.close()
    return end
