# Architecture & State Lifecycle

This document complements [PAPER_NOTES.md](../PAPER_NOTES.md) by describing the runtime dataflow, per-repository state files, and arm isolation in `econ-wrap`.

## Black-Box Boundary

`econ/agent.py` implements a sealed ReAct coding loop (`run_agent`) that never imports `econ/controller.py`. The controller interacts with the agent exclusively through three external surfaces:

1. **Instruction prefix (`skills.md`):** Prepended ahead of the task prompt by `agent.render_prompt()`.
2. **Tool allowlist (`allowlist.json` / `settings.json`):** Filters which of the 12 tools in `econ/tools.py` are exposed in the system prompt schema.
3. **Live step hook (`on_step`):** Invoked after each step so `LoopGuard` can inspect recent tool calls and return an optional `[controller note]` redirect without aborting the turn.

## Per-Repository State (`~/.econ/<repo-hash>/`)

`controller.State` stores all learned state under `$ECON_HOME/<repo-hash>/` (default `~/.econ/<repo-hash>/`):

| File / Directory | Purpose |
|---|---|
| `sessions/*.jsonl` | Append-only step and session-end traces (commands, outputs, token usage, controller tokens, redirects) |
| `candidates.json` | Candidate micro-skills with `confirm` regexes, confirming session IDs, contradiction counts, and status (`candidate`, `promoted`, `retired`) |
| `skills.md` | Rendered micro-skill manual injected into future sessions once a candidate reaches `MIN_CONFIRM = 3` distinct sessions with non-negative support (`confirms - 5 * contradictions >= 0`) |
| `allowlist.json` | Currently pruned non-protected tools (unused for `N = 4` consecutive sessions) and restore markers |

Protected core tools (`tools.PROTECTED`) are never pruned. Running `./bin/econ restore-tools` clears `allowlist.json` and resets unused streaks.

## Evaluation Arms & Isolation

`econ/report.py` evaluates five arms defined in `controller.ARMS`:

- `baseline`: All three levers disabled.
- `controlled`: Micro-skills + tool pruning + loop guard enabled.
- `skills-only`, `prune-only`, `guard-only`: Single-lever ablations.

Each arm runs against its own isolated state root (`runs/report/arms/<arm>/home/<repo-hash>/`) and throwaway toy repository seeded by `econ/toyrepo.py`. Completed sessions are detected on disk so interrupted `econ report` runs resume automatically, and `--render-only` regenerates `runs/report/report.html` without making model calls.
