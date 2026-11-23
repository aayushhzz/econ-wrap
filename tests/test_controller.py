import ast
import importlib.util
import json
import os
import sysconfig

from conftest import FIX_TASK1, MINER_FACTS, scripted_agent

from econ import agent, cli, controller as ctl, report, toyrepo
from econ.tools import ALL_TOOLS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- done criterion: stdlib only

def test_controller_core_imports_only_stdlib():
    stdlib = os.path.realpath(sysconfig.get_paths()["stdlib"])
    bad = []
    pkg = os.path.join(ROOT, "econ")
    for fn in sorted(os.listdir(pkg)):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(pkg, fn)).read())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module.split(".")[0]]
            for name in names:
                spec = importlib.util.find_spec(name)
                origin = spec.origin if spec else None
                ok = spec is not None and (origin in (None, "built-in", "frozen") or (
                    os.path.realpath(origin).startswith(stdlib) and "site-packages" not in origin))
                if not ok:
                    bad.append("%s imports %s" % (fn, name))
    assert not bad, bad


# ---------------------------------------------------------------- done criterion: promotion needs >= 3 sessions

def _cmd(c, held=True, contradicted=False):
    return {"cmd": c, "rc": 0 if held else 1, "held": held, "contradicted": contradicted}


def test_fact_promoted_only_after_three_distinct_sessions():
    ledger = {}
    prop = [{"id": "Tests-Need-PYTHONPATH", "fact": "Use PYTHONPATH=src.", "confirm": "PYTHONPATH=src"}]
    s1 = [_cmd("PYTHONPATH=src python -m pytest -q"), _cmd("PYTHONPATH=src python -m pytest -q")]
    assert ctl.admit_proposals(ledger, prop, 1, s1, {}) == ["tests-need-pythonpath"]
    ctl.record_session(ledger, 1, s1)
    ctl.record_session(ledger, 1, s1)  # the same session again must not count twice
    assert ledger["tests-need-pythonpath"]["confirmations"] == [1]
    ctl.record_session(ledger, 2, [_cmd("PYTHONPATH=src pytest")])
    ctl.record_session(ledger, 3, [_cmd("ls")])  # not exercised: no evidence either way
    assert ledger["tests-need-pythonpath"]["status"] == "candidate"
    assert ctl.render_manual(ledger) == ""
    ctl.record_session(ledger, 4, [_cmd("PYTHONPATH=src python -m pytest tests/test_x.py")])
    e = ledger["tests-need-pythonpath"]
    assert e["confirmations"] == [1, 2, 4] and e["status"] == "promoted" and e["promoted_after_session"] == 4
    assert "Use PYTHONPATH=src." in ctl.render_manual(ledger)


def test_ungrounded_and_contradicted_facts():
    ledger = {}
    s1 = [_cmd("make fmt"), _cmd("black .", held=False, contradicted=True)]
    props = [{"id": "tox", "fact": "Run tox.", "confirm": r"\btox\b"},           # never ran
             {"id": "bad-rx", "fact": "x", "confirm": "("},                       # invalid regex
             {"id": "use-black", "fact": "Format with black.", "confirm": "black"},  # ran, but failed
             {"id": "use-make-fmt", "fact": "Use make fmt.", "confirm": "make fmt"}]
    assert ctl.admit_proposals(ledger, props, 1, s1, {}) == ["use-make-fmt"]
    # retroactive evidence from earlier sessions counts on admission
    ledger2 = {}
    hist = {1: [_cmd("make fmt")], 2: [_cmd("make fmt")]}
    ctl.admit_proposals(ledger2, props[3:], 3, [_cmd("make fmt")], hist)
    assert ledger2["use-make-fmt"]["confirmations"] == [1, 2]
    ctl.record_session(ledger2, 3, [_cmd("make fmt")])
    assert ledger2["use-make-fmt"]["status"] == "promoted"
    # one contradiction outweighs the confirmations -> retired, never re-injected
    ctl.record_session(ledger2, 4, [_cmd("make fmt", held=False, contradicted=True)])
    assert ledger2["use-make-fmt"]["status"] == "retired" and ctl.render_manual(ledger2) == ""


def test_session_commands_classifies_output():
    ev = [{"type": "step", "tool": "run_shell", "args": {"command": "pytest"}, "output": "exit=2\nE   ModuleNotFoundError: No module named 'inventory'"},
          {"type": "step", "tool": "run_shell", "args": {"command": "PYTHONPATH=src pytest"}, "output": "exit=1\n1 failed, 1 passed in 0.1s"},
          {"type": "step", "tool": "run_shell", "args": {"command": "black ."}, "output": "exit=127\nsh: black: command not found"}]
    c = ctl.session_commands(ev)
    assert [(x["held"], x["contradicted"]) for x in c] == [(False, True), (True, False), (False, True)]


def test_real_run_with_fake_backend_writes_candidates_and_injects_skills(tmp_home):
    home, repo = tmp_home
    be = scripted_agent(FIX_TASK1, miner=MINER_FACTS)
    ends = [ctl.run_session(t, repo, home, be, levers=("skills",)) for t in (1, 1, 1)]
    assert ends[0]["passed"]  # the scripted fix really passes the pytest check
    state = ctl.State(home, repo)
    led = json.load(open(state.path("candidates.json")))
    assert "hallucinated" not in led
    assert led["tests-need-pythonpath"]["confirmations"] == [1, 2, 3]
    assert led["tests-need-pythonpath"]["status"] == "promoted"
    # sessions 1-3 ran without the manual; session 4 gets it as the prompt prefix
    assert all("repository manual" not in p for _, p, r in be.calls if r == "agent")
    ctl.run_session(1, repo, home, be, levers=("skills",))
    last_agent_prompt = [p for _, p, r in be.calls if r == "agent"][-1]
    assert last_agent_prompt.startswith("# repository manual") and "PYTHONPATH=src" in last_agent_prompt
    start = state.read_events(state.session_files()[-1])[0]
    assert set(start["skills"]) == {"tests-need-pythonpath", "use-make-fmt"}


# ---------------------------------------------------------------- done criterion: pruning + restore-tools

def test_pruned_tools_leave_schema_and_restore_brings_them_back(tmp_home):
    home, repo = tmp_home
    be = scripted_agent([("list_dir", {"path": "."}), ("finish", {"summary": "x"})])

    def last_system():
        return [s for s, _, r in be.calls if r == "agent"][-1]

    for _ in range(3):
        ctl.run_session(1, repo, home, be, levers=("prune",))
    assert '"name": "http_get"' in last_system()
    end = ctl.run_session(1, repo, home, be, levers=("prune",))  # 4th unused session -> prune
    assert "http_get" in end["newly_pruned"] and "list_dir" not in end["newly_pruned"]
    assert "run_shell" not in end["newly_pruned"]  # protected core tool
    allow = json.load(open(ctl.State(home, repo).path("allowlist.json")))
    assert allow["pruned"]["http_get"] == [1, 2, 3, 4]
    ctl.run_session(1, repo, home, be, levers=("prune",))
    sys5 = last_system()
    assert '"name": "http_get"' not in sys5 and '"name": "grep"' not in sys5 and '"name": "run_shell"' in sys5
    assert cli.main(["restore-tools", "--home", home, "--workdir", repo]) == 0
    ctl.run_session(1, repo, home, be, levers=("prune",))
    sys6 = last_system()
    assert all('"name": "%s"' % t in sys6 for t in ALL_TOOLS)
    # streaks restart after a restore: one session is not enough to prune again
    assert json.load(open(ctl.State(home, repo).path("allowlist.json")))["pruned"] == {}


# ---------------------------------------------------------------- done criterion: loop guard injects, turn continues

def test_loop_guard_redirect_is_injected_and_the_turn_continues(tmp_home):
    home, repo = tmp_home
    stuck = [("run_shell", {"command": "python -m pytest -q"})]  # same failing command forever
    be = scripted_agent(stuck, judge={"stuck": True, "confidence": 0.9,
                                      "redirect": "You re-ran the same failing pytest; check how tests import the package."})
    end = ctl.run_session(1, repo, home, be, levers=("guard",), max_steps=7)
    state = ctl.State(home, repo)
    ev = state.read_events(state.session_files()[-1])
    inj = [e for e in ev if e["type"] == "inject"]
    assert len(inj) == 1 and inj[0]["step"] == 3 and inj[0]["text"].startswith("Loop guard:")
    assert [e["step"] for e in ev if e["type"] == "step"] == [1, 2, 3, 4, 5, 6, 7]  # not terminated
    assert end["redirects"] == 1 and end["controller_tokens"] > 0
    prompts = [p for _, p, r in be.calls if r == "agent"]
    assert "[controller note] Loop guard:" in prompts[3] and "[controller note]" not in prompts[2]


def test_rule_trigger_stays_closed_on_progress():
    steps = [{"step": 1, "tool": "read_file", "args": {"path": "a"}, "ok": True, "output": "x"},
             {"step": 2, "tool": "run_shell", "args": {"command": "make fmt"}, "ok": True, "output": "exit=0"},
             {"step": 3, "tool": "run_shell", "args": {"command": "pytest"}, "ok": False, "output": "exit=1\nFAILED t.py"}]
    assert ctl.rule_trigger(steps) == ""
    steps.append({"step": 4, "tool": "run_shell", "args": {"command": "pytest -x"}, "ok": False, "output": "exit=1\nFAILED t.py"})
    assert ctl.rule_trigger(steps).startswith("repeated failure signature")
    # repeats are counted over the whole trajectory, not just the recent window
    far = [steps[3]] + [{"step": i, "tool": "read_file", "args": {"path": str(i)}, "ok": True, "output": "x"}
                        for i in range(5, 12)] + [dict(steps[3], step=12)]
    assert ctl.rule_trigger(far).startswith("repeated failure signature")


# ---------------------------------------------------------------- agent + report helpers

def test_parse_action():
    assert agent.parse_action('```json\n{"tool": "finish", "args": {}}\n```')["tool"] == "finish"
    assert agent.parse_action('thinking {not json} then {"thought": "t", "tool": "grep", "args": {"pattern": "x"}}')["tool"] == "grep"
    assert agent.parse_action("no json here") is None


def test_break_even():
    assert report.break_even([10, 10, 10, 10], [15, 8, 6, 5]) == 3  # 15>10, 23>20, 29<=30, 34<=40
    assert report.break_even([10, 10], [12, 9]) is None             # 21 > 20 at the end
    assert report.break_even([10, 10], [5, 20]) is None
    assert report.break_even([10, 10], [9, 9]) == 1


def test_report_renders_curves_and_break_even(tmp_path, tmp_home):
    home, _ = tmp_home
    out = str(tmp_path / "rep")
    be = scripted_agent(FIX_TASK1, miner=MINER_FACTS)
    errs = report.run_all(out, [1, 4], ["baseline", "controlled"], be, max_steps=6)
    assert errs == []
    data = report.collect(out, ["baseline", "controlled"])
    report.render(data, ["baseline", "controlled"], str(tmp_path / "r.html"), "fake")
    page = open(str(tmp_path / "r.html")).read()
    assert page.count("<polyline") == 2 and "Break-even task index" in page and "passed" in page


def test_every_task_seeds_into_a_failing_state(tmp_path):
    for tid in (1, 3, 7, 12):
        repo = toyrepo.seed(str(tmp_path / ("r%d" % tid)), tid)
        ok, _ = toyrepo.check(repo, tid)
        assert not ok


def test_loop_guard_skips_judge_on_the_final_step(tmp_home):
    home, repo = tmp_home
    be = scripted_agent([("run_shell", {"command": "python -m pytest -q"})])  # judge always says not stuck
    ctl.run_session(1, repo, home, be, levers=("guard",), max_steps=6)
    state = ctl.State(home, repo)
    judged = [e["step"] for e in state.read_events(state.session_files()[-1]) if e["type"] == "guard" and e["judged"]]
    assert judged == [3]  # step 6 is the last step: nothing could follow a redirect


def test_rule_trigger_reports_every_firing_counter():
    same = {"tool": "replace_in_file", "args": {"path": "a.py", "old": "x", "new": "y"}, "ok": False,
            "output": "error: old string not found in a.py"}
    reason = ctl.rule_trigger([dict(same, step=i) for i in (1, 2, 3)])
    assert "repeated failure signature" in reason and "same failing action was repeated (most repeated: 3x)" in reason
    assert "rewriting the same file" in reason
