"""econ run | econ report | econ restore-tools"""
import argparse
import os
import sys

from . import controller as ctl
from . import report as rep
from . import toyrepo
from .backend import get_backend

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_tasks(spec):
    if "-" in spec:
        a, b = spec.split("-", 1)
        ids = list(range(int(a), int(b) + 1))
    else:
        ids = [int(x) for x in spec.split(",")]
    bad = [i for i in ids if i not in toyrepo.TASK_BY_ID]
    if bad:
        raise SystemExit("unknown task ids: %s (valid 1-13; 13 is the loop probe)" % bad)
    return ids


def main(argv=None):
    p = argparse.ArgumentParser(prog="econ", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run tasks through the agent, wrapped by the controller")
    r.add_argument("--tasks", default="1", help="e.g. 3, 1-6 or 1,4,5")
    r.add_argument("--arm", default="controlled", choices=sorted(ctl.ARMS))
    r.add_argument("--workdir", default=os.path.join(PROJECT, "work", "toyrepo"))
    r.add_argument("--home", default=None, help="state dir (default $ECON_HOME or ~/.econ)")
    r.add_argument("--max-steps", type=int, default=12)
    g = sub.add_parser("report", help="baseline vs controlled + per-lever ablations -> report.html")
    g.add_argument("--tasks", default="1-6")
    g.add_argument("--arms", default=",".join(["baseline", "controlled", "skills-only", "prune-only", "guard-only"]))
    g.add_argument("--out", default=os.path.join(PROJECT, "runs", "report"))
    g.add_argument("--max-steps", type=int, default=12)
    g.add_argument("--render-only", action="store_true", help="re-render report.html from existing results")
    t = sub.add_parser("restore-tools", help="bring every pruned tool back into the agent's tool list")
    t.add_argument("--workdir", default=os.path.join(PROJECT, "work", "toyrepo"))
    t.add_argument("--home", default=None)
    a = p.parse_args(argv)

    if a.cmd == "run":
        backend = get_backend()
        home = a.home or ctl.default_home()
        for tid in parse_tasks(a.tasks):
            end = ctl.run_session(tid, a.workdir, home, backend, levers=ctl.ARMS[a.arm], max_steps=a.max_steps,
                                  arm=a.arm)
            print("task %d: %s in %d steps, %d tokens (agent %d + controller %d), redirects %d, newly pruned %s" % (
                tid, "PASS" if end["passed"] else "FAIL", end["steps"], end["total_tokens"], end["agent_tokens"],
                end["controller_tokens"], end["redirects"], end["newly_pruned"] or "-"))
        print("state:", ctl.State(home, a.workdir).dir)
        return 0
    if a.cmd == "report":
        arms = [x for x in a.arms.split(",") if x]
        for x in arms:
            if x not in ctl.ARMS:
                raise SystemExit("unknown arm %r" % x)
        tasks = parse_tasks(a.tasks)
        os.makedirs(a.out, exist_ok=True)
        if not a.render_only:
            errors = rep.run_all(a.out, tasks, arms, get_backend(), a.max_steps)
            for err in errors:
                print("arm error:", err, file=sys.stderr)
        data = rep.collect(a.out, arms)
        meta = "backend=%s agent_model=%s small_model=%s tasks=%s max_steps=%d" % (
            os.environ.get("ECON_BACKEND", "claude"), os.environ.get("ECON_MODEL", "(CLI default)"),
            os.environ.get("ECON_SMALL_MODEL", "haiku"), a.tasks, a.max_steps)
        out_path = os.path.join(a.out, "report.html")
        be = rep.render(data, arms, out_path, meta)
        for arm in arms:
            rs = data.get(arm, {}).get("results", [])
            print("%-12s passed %d/%d  total tokens %s" % (arm, sum(r["passed"] for r in rs), len(rs),
                                                          "{:,}".format(sum(r["total_tokens"] for r in rs))))
        print("break-even task index (controlled vs baseline):", be or "not reached")
        print("wrote", out_path)
        return 0
    if a.cmd == "restore-tools":
        state = ctl.State(a.home or ctl.default_home(), a.workdir)
        restored = state.restore_tools()
        print("restored:", ", ".join(restored) or "(nothing was pruned)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
