"""`econ report`: run baseline vs controlled (+ one ablation per lever) over one task sequence, write report.html."""
import html
import json
import os
import threading

from . import controller as ctl

COLORS = {"baseline": "var(--c-base)", "controlled": "var(--c-ctrl)", "skills-only": "var(--c-a1)",
          "prune-only": "var(--c-a2)", "guard-only": "var(--c-a3)"}


def cumulative(xs):
    out, s = [], 0
    for x in xs:
        s += x
        out.append(s)
    return out


def break_even(base, other):
    """Smallest 1-based task index from which `other`'s cumulative cost stays <= baseline's; None if never."""
    cb, co = cumulative(base), cumulative(other)
    idx = None
    for i in range(len(cb) - 1, -1, -1):
        if co[i] <= cb[i]:
            idx = i + 1
        else:
            break
    return idx


def arm_dirs(out_dir, arm):
    root = os.path.join(out_dir, "arms", arm)
    return os.path.join(root, "home"), os.path.join(root, "repo"), os.path.join(root, "results.json")


def run_arm(out_dir, arm, tasks, backend, max_steps, errors):
    home, repo, res_path = arm_dirs(out_dir, arm)
    os.makedirs(home, exist_ok=True)
    try:
        with open(res_path) as f:
            results = json.load(f)
    except (OSError, ValueError):
        results = []
    done = {r["task"] for r in results}  # resume: a rerun skips finished tasks
    try:
        for t in tasks:
            if t in done:
                continue
            end = ctl.run_session(t, repo, home, backend, levers=ctl.ARMS[arm], max_steps=max_steps, arm=arm)
            results.append(end)
            with open(res_path, "w") as f:
                json.dump(results, f, indent=2)
            print("[%s] task %d: %s, %d steps, %d tokens (controller %d)%s" % (
                arm, t, "PASS" if end["passed"] else "fail", end["steps"], end["total_tokens"],
                end["controller_tokens"], ", %d redirect" % end["redirects"] if end["redirects"] else ""), flush=True)
    except Exception as e:  # keep other arms running; reported at the end
        errors.append("%s: %s" % (arm, e))


def run_all(out_dir, tasks, arms, backend, max_steps):
    errors, threads = [], []
    for arm in arms:
        th = threading.Thread(target=run_arm, args=(out_dir, arm, tasks, backend, max_steps, errors))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    return errors


def collect(out_dir, arms):
    data = {}
    for arm in arms:
        home, repo, res_path = arm_dirs(out_dir, arm)
        try:
            with open(res_path) as f:
                results = json.load(f)
        except (OSError, ValueError):
            continue
        state = ctl.State(home, repo)
        sessions = state.all_sessions()
        injections = []
        for sid, ev in sorted(sessions.items()):
            steps = [e["step"] for e in ev if e.get("type") == "step"]
            for e in ev:
                if e.get("type") == "inject":
                    injections.append({"session": sid, "task": ev[0]["task"], "step": e["step"], "text": e["text"],
                                       "steps_after": len([s for s in steps if s > e["step"]])})
        data[arm] = {"results": sorted(results, key=lambda r: r["session"]), "ledger": state.ledger(),
                     "allowlist": state.allowlist(), "injections": injections, "state_dir": state.dir}
    return data


def _chart(data, arms):
    W, H, L, R, T, B = 720, 340, 70, 130, 20, 40
    series = {a: cumulative([r["total_tokens"] for r in data[a]["results"]]) for a in arms if a in data}
    n = max((len(s) for s in series.values()), default=1)
    ymax = max((s[-1] for s in series.values() if s), default=1) * 1.08 or 1

    def x(i):
        return L + (W - L - R) * (i / max(n - 1, 1))

    def y(v):
        return T + (H - T - B) * (1 - v / ymax)

    parts = ['<svg viewBox="0 0 %d %d" role="img" aria-label="Cumulative tokens by task index">' % (W, H)]
    for k in range(5):
        v = ymax * k / 4
        parts.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>' % (L, W - R, y(v), y(v)))
        parts.append('<text x="%d" y="%.1f" class="ax" text-anchor="end">%s</text>' % (L - 8, y(v) + 4, "{:,}".format(int(v))))
    for i in range(n):
        parts.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%d</text>' % (x(i), H - B + 18, i + 1))
    parts.append('<text x="%d" y="%d" class="ax" text-anchor="middle">task index</text>' % ((L + W - R) // 2, H - 4))
    labels = []
    for a, s in series.items():
        pts = " ".join("%.1f,%.1f" % (x(i), y(v)) for i, v in enumerate(s))
        wide = a in ("baseline", "controlled")
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%s" %s/>' % (
            pts, COLORS[a], 3 if wide else 1.6, "" if wide else 'stroke-dasharray="5 4"'))
        for i, v in enumerate(s):
            parts.append('<circle cx="%.1f" cy="%.1f" r="%s" fill="%s"><title>%s task %d: %s</title></circle>' % (
                x(i), y(v), 3.5 if wide else 2.5, COLORS[a], a, i + 1, "{:,}".format(v)))
        if s:
            labels.append([y(s[-1]), a])
    labels.sort()
    for j in range(1, len(labels)):  # keep end labels from overlapping
        labels[j][0] = max(labels[j][0], labels[j - 1][0] + 14)
    for ly, a in labels:
        parts.append('<text x="%d" y="%.1f" class="lab" fill="%s">%s</text>' % (W - R + 8, ly + 4, COLORS[a], a))
    parts.append("</svg>")
    return "\n".join(parts)


def render(data, arms, out_path, meta):
    e = html.escape
    base = [r["total_tokens"] for r in data.get("baseline", {}).get("results", [])]
    rows = []
    for a in arms:
        if a not in data:
            continue
        rs = data[a]["results"]
        tot = sum(r["total_tokens"] for r in rs)
        btot = sum(base[:len(rs)]) if base else 0
        be = break_even(base[:len(rs)], [r["total_tokens"] for r in rs]) if base and a != "baseline" else None
        rows.append("<tr><td><span class='sw' style='background:%s'></span>%s</td><td>%d / %d</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td></tr>" % (
                        COLORS[a], e(a), sum(r["passed"] for r in rs), len(rs), "{:,}".format(tot),
                        "{:,}".format(sum(r["controller_tokens"] for r in rs)),
                        "$%.4f" % sum(r.get("cost_usd", 0) for r in rs),
                        "-" if a == "baseline" or not btot else "%+.1f%%" % (100.0 * (tot - btot) / btot),
                        "-" if a == "baseline" else (str(be) if be else "not reached"),
                        sum(r["redirects"] for r in rs)))
    per_task = []
    n = max((len(d["results"]) for d in data.values()), default=0)
    for i in range(n):
        cells = []
        for a in arms:
            rs = data.get(a, {}).get("results", [])
            if i < len(rs):
                r = rs[i]
                cells.append("<td class='%s'>%s<br><small>%d steps</small></td>" % (
                    "pass" if r["passed"] else "fail", "{:,}".format(r["total_tokens"]), r["steps"]))
            else:
                cells.append("<td>-</td>")
        task = next((d["results"][i]["task"] for d in data.values() if i < len(d["results"])), "?")
        per_task.append("<tr><td>%d (task %s)</td>%s</tr>" % (i + 1, task, "".join(cells)))
    ctrl = data.get("controlled", {})
    skills = []
    for fid, en in sorted(ctrl.get("ledger", {}).items(), key=lambda kv: kv[1].get("promoted_after_session", 99)):
        skills.append("<tr><td><code>%s</code></td><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td></tr>" % (
            e(fid), e(en["fact"]), e(en["confirm"]), e(", ".join(map(str, en["confirmations"])) or "-"),
            e(en["status"] + (" after session %s" % en["promoted_after_session"] if en.get("promoted_after_session") else "")
              + ("; contradicted in sessions %s" % ", ".join(map(str, en["contradictions"])) if en["contradictions"] else ""))))
    pruned = []
    for a in ("controlled", "prune-only"):
        for tool, sids in sorted(data.get(a, {}).get("allowlist", {}).get("pruned", {}).items()):
            pruned.append("<tr><td>%s</td><td><code>%s</code></td><td>unused in sessions %s</td></tr>" % (
                e(a), e(tool), e(", ".join(map(str, sids)))))
    inj = []
    for a in arms:
        for j in data.get(a, {}).get("injections", []):
            inj.append("<tr><td>%s</td><td>%d (task %d)</td><td>%d</td><td>%s</td><td>%d</td></tr>" % (
                e(a), j["session"], j["task"], j["step"], e(j["text"]), j["steps_after"]))
    be_main = break_even(base, [r["total_tokens"] for r in ctrl.get("results", [])]) if base and ctrl else None
    page = TEMPLATE.format(
        meta=e(meta), chart=_chart(data, arms),
        headline=("Break-even task index (controlled vs baseline): <b>%s</b>" % (be_main if be_main else "not reached in this run")),
        summary="".join(rows), head="".join("<th>%s</th>" % e(a) for a in arms if a in data),
        per_task="".join(per_task), skills="".join(skills) or "<tr><td colspan=5>none</td></tr>",
        pruned="".join(pruned) or "<tr><td colspan=3>none</td></tr>",
        inj="".join(inj) or "<tr><td colspan=5>none</td></tr>")
    with open(out_path, "w") as f:
        f.write(page)
    return be_main


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>econ cost report</title>
<style>
:root {{ --bg:#fbfbfa; --fg:#1d1d1f; --muted:#6b6b70; --line:#e3e3e0; --card:#fff;
  --c-base:#8a8a8f; --c-ctrl:#2563eb; --c-a1:#d97706; --c-a2:#059669; --c-a3:#c026d3; --pass:#e7f6ec; --fail:#fdecec; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#141416; --fg:#ececee; --muted:#9a9aa2;
  --line:#2c2c31; --card:#1c1c20; --c-base:#9a9aa2; --c-ctrl:#60a5fa; --c-a1:#fbbf24; --c-a2:#34d399; --c-a3:#e879f9;
  --pass:#16301f; --fail:#3a1b1b; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system, system-ui, sans-serif; margin:0; }}
main {{ max-width:980px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:32px 0 8px; }}
.meta {{ color:var(--muted); font-size:13px; }} .headline {{ font-size:17px; margin:16px 0; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; overflow-x:auto; }}
svg {{ width:100%; height:auto; display:block; }} .grid {{ stroke:var(--line); }}
.ax {{ fill:var(--muted); font-size:11px; }} .lab {{ font-size:12px; font-weight:600; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }} th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; }} td.pass {{ background:var(--pass); }} td.fail {{ background:var(--fail); }}
.sw {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; }}
code {{ font-size:12px; }} small {{ color:var(--muted); }}
</style></head><body><main>
<h1>Black-box cost control: baseline vs controlled</h1>
<div class="meta">{meta}</div>
<p class="headline">{headline}</p>
<div class="card">{chart}</div>
<h2>Arms</h2>
<div class="card"><table><tr><th>arm</th><th>passed</th><th>total tokens</th><th>controller tokens</th><th>notional cache-aware $</th><th>tokens vs baseline</th><th>break-even task</th><th>redirects</th></tr>{summary}</table></div>
<h2>Per task (tokens incl. controller overhead; green = pytest check passed)</h2>
<div class="card"><table><tr><th>index</th>{head}</tr>{per_task}</table></div>
<h2>Micro-skill ledger (controlled arm)</h2>
<p class="meta">A fact reaches skills.md only after its confirm regex matched a command that held in 3 or more distinct sessions.</p>
<div class="card"><table><tr><th>id</th><th>fact</th><th>detector</th><th>confirming sessions</th><th>status</th></tr>{skills}</table></div>
<h2>Pruned tools</h2>
<div class="card"><table><tr><th>arm</th><th>tool</th><th>justification</th></tr>{pruned}</table></div>
<h2>Loop-guard redirects</h2>
<div class="card"><table><tr><th>arm</th><th>session</th><th>after step</th><th>injected text</th><th>steps run afterwards</th></tr>{inj}</table></div>
</main></body></html>
"""
