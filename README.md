# econ-wrap

`econ-wrap` is a small, runnable version of **TokenEconomizer: Experience-Driven Black-Box Cost Control for Coding Agents** (Li et al., arXiv [2610.22732](https://arxiv.org/abs/2610.22732), Microsoft Research and UC Berkeley, 2026).

The controller is written in standard-library-only Python 3.9. It wraps an unmodified ReAct coding agent whose model is **Claude**, called through your locally logged-in Claude Code CLI on your subscription. Over a sequence of tasks on one repository, the controller learns three things:

- which repo facts to inject (micro-skills),
- which tools to hide (pruning),
- when to nudge the agent out of a failure loop (loop guard).

It then reports whether cumulative token cost falls below the uncontrolled baseline.

## Why the paper matters

Agents are usually benchmarked one task at a time, but people use them on one repo for weeks. In that setting the agent keeps paying the same costs:

- it rediscovers the test command,
- it re-reads tool schemas it never calls,
- it burns turns in loops.

Each turn re-bills the whole prefix and history, so these costs repeat. TokenEconomizer removes them from outside the agent: no model access, no changes to the agent loop. It only uses the prompt prefix, the tool configuration and the event stream. The paper reports 18 to 31% lower cost on SWE-Bench across Claude Code, Codex and Copilot CLI, with comparable pass rates. It costs more at first while the controller learns, and less once it has.

See `PAPER_NOTES.md` for the mechanism, a map from paper concepts to the code here, and what I simplified.

## Layout

```
econ/agent.py       black-box ReAct agent; configured only via settings.json + an on_step hook
econ/tools.py       12 tools (5 deliberately irrelevant: http_get, sql_query, image_resize, send_email, translate_text)
econ/controller.py  the three levers, per-repo state, session runner
econ/toyrepo.py     seeded toy repo with quirks + 12 bug-fix/feature tasks, each with a pytest check
econ/report.py      arms, cumulative curves, break-even, report.html
econ/backend.py     one interface for model calls: claude CLI (default), ollama (optional), fake (tests)
bin/econ            wrapper: runs `python -m econ` with the project venv
tests/              offline tests (fake backend, no Claude/Ollama calls)
paper/              the paper PDF
```

The toy repo has three quirks the agent must rediscover each session unless it is taught:

1. Tests only import with `PYTHONPATH=src`.
2. `tests/fixtures/` must first be generated with `scripts/gen_fixtures.py`.
3. A CI style test fails unless `make fmt` (not black, which is not installed) stamped the sources.

## Setup

Requirements: macOS or Linux, Python 3.9+, and the Claude Code CLI logged in with a subscription (`claude auth status` shows `authMethod: claude.ai`). No API key is needed, and the code never reads or sets `ANTHROPIC_API_KEY`.

```sh
cd econ-wrap
python3 -m venv .venv && .venv/bin/pip install pytest   # pytest is only for the toy repo's checks and our tests
```

The controller itself imports nothing outside the standard library, and a test enforces this. The venv exists because the toy repo's task checks are pytest suites.

## Run

```sh
./bin/econ run --tasks 1-3                 # controlled agent on tasks 1-3; state in ~/.econ/<repo-hash>/
./bin/econ run --tasks 4 --arm baseline    # same agent, no controller
./bin/econ restore-tools                   # undo tool pruning for that repo
./bin/econ report --tasks 1-6              # baseline, controlled and 3 ablations -> runs/report/report.html
./bin/econ report --render-only            # re-render from saved results
```

Environment knobs:

- `ECON_MODEL` sets the agent model. It is unset by default, which means the Claude CLI's default model; you can pass `haiku`, `sonnet` or a full model id.
- `ECON_SMALL_MODEL` sets the miner and judge model (default `haiku`).
- `ECON_HOME` sets the state root (default `~/.econ`).
- `ECON_BACKEND=ollama` switches to the optional local backend, with `qwen3:8b` as the agent and `qwen3:4b-instruct` as the small model.

`econ report` keeps each arm's state separate, under `runs/report/arms/<arm>/home/<repo-hash>/`. A rerun skips sessions that already finished, so an interrupted report can resume.

Each Claude call is `claude -p --output-format json` with `--tools ""`, a custom `--system-prompt`, `--setting-sources ""` and `--settings '{"disableAllHooks": true}'`, `--strict-mcp-config` and `--no-session-persistence`. That means no built-in tools, none of your hooks, CLAUDE.md or MCP servers, and no saved sessions. Only our own tools run, inside the throwaway toy repo.

## Tests

```sh
.venv/bin/python -m pytest -q tests
```

The tests run offline with a scripted fake backend. They cover:

- a stdlib-only import check;
- promotion only after 3 distinct confirming sessions (same-session duplicates, ungrounded or invalid regexes, retroactive evidence, retirement on contradiction);
- a real fake-backend run where `candidates.json` reaches 3 confirmations and `skills.md` is injected in session 4;
- pruned tools disappearing from the agent's tool schema and `econ restore-tools` bringing them back;
- a loop-guard redirect being injected while the session keeps running;
- break-even math, report rendering, and every task seeding into a failing state.

## Demo output

One real run on 2026-11-20, using Claude through the local CLI on the subscription:

```sh
ECON_MODEL=haiku ./bin/econ report --tasks 1-6 --max-steps 12
```

I set the agent model to `haiku` to keep Pro usage small. The run used 5 arms × 6 sessions, about 1.4M tokens in total (mostly cache reads); the CLI's notional cache-aware price for the whole run is about $0.20. It took about 30 minutes with the arms in parallel. The full console output is in `runs/report/demo_output.txt`:

```
[baseline] task 1: PASS, 12 steps, 47205 tokens (controller 0)
[prune-only] task 1: PASS, 12 steps, 47215 tokens (controller 0)
[guard-only] task 1: fail, 12 steps, 45390 tokens (controller 0)
[skills-only] task 1: PASS, 11 steps, 45744 tokens (controller 2398)
[controlled] task 1: PASS, 12 steps, 52077 tokens (controller 5637)
[prune-only] task 2: PASS, 11 steps, 43614 tokens (controller 0)
[baseline] task 2: fail, 12 steps, 48656 tokens (controller 0)
[skills-only] task 2: fail, 12 steps, 48870 tokens (controller 2708)
[guard-only] task 2: PASS, 12 steps, 53342 tokens (controller 3474)
[controlled] task 2: PASS, 12 steps, 56780 tokens (controller 6552)
[prune-only] task 3: PASS, 12 steps, 54302 tokens (controller 0)
[baseline] task 3: PASS, 12 steps, 49596 tokens (controller 0)
[skills-only] task 3: PASS, 12 steps, 55321 tokens (controller 2637)
[guard-only] task 3: fail, 12 steps, 53533 tokens (controller 3340)
[prune-only] task 4: PASS, 9 steps, 32846 tokens (controller 0)
[baseline] task 4: PASS, 12 steps, 45730 tokens (controller 0)
[controlled] task 3: PASS, 12 steps, 56683 tokens (controller 5968)
[skills-only] task 4: PASS, 11 steps, 44298 tokens (controller 2818)
[guard-only] task 4: PASS, 10 steps, 38194 tokens (controller 1621)
[prune-only] task 5: PASS, 12 steps, 38765 tokens (controller 0)
[controlled] task 4: PASS, 9 steps, 36812 tokens (controller 4476)
[baseline] task 5: PASS, 12 steps, 48062 tokens (controller 0)
[skills-only] task 5: PASS, 12 steps, 51561 tokens (controller 3114)
[prune-only] task 6: PASS, 12 steps, 38395 tokens (controller 0)
[guard-only] task 5: PASS, 12 steps, 53977 tokens (controller 3187)
[baseline] task 6: PASS, 12 steps, 47045 tokens (controller 0)
[controlled] task 5: PASS, 12 steps, 44725 tokens (controller 3410)
[skills-only] task 6: PASS, 11 steps, 46079 tokens (controller 3057)
[guard-only] task 6: PASS, 12 steps, 52347 tokens (controller 3229)
[controlled] task 6: PASS, 9 steps, 30634 tokens (controller 3002)
baseline     passed 5/6  total tokens 286,294
controlled   passed 6/6  total tokens 277,711
skills-only  passed 5/6  total tokens 291,873
prune-only   passed 6/6  total tokens 255,137
guard-only   passed 4/6  total tokens 296,783
break-even task index (controlled vs baseline): 6
wrote /Users/aayush/Desktop/Projects/econ-wrap/runs/report/report.html
exit=0
```

What `runs/report/report.html` shows for this run:

| arm | passed | total tokens | tokens vs baseline | notional cache-aware $ | break-even task |
|---|---|---|---|---|---|
| baseline | 5/6 | 286,294 | - | $0.0390 | - |
| controlled | 6/6 | 277,711 (29,045 controller) | -3.0% | $0.0429 | 6 |
| skills-only | 5/6 | 291,873 | +1.9% | $0.0418 | not reached |
| prune-only | 6/6 | 255,137 | -10.9% | $0.0379 | 2 |
| guard-only | 4/6 | 296,783 | +3.7% | $0.0410 | not reached |

- **Micro-skills (controlled arm).** Three facts were promoted, each only after 3 or more distinct confirming sessions:
  - `PYTHONPATH=src python -m pytest`: confirmed in sessions 1, 2, 3, promoted after session 3.
  - `python scripts/fmt.py`: confirmed in sessions 2, 4, 5, promoted after session 5.
  - `python scripts/gen_fixtures.py`: confirmed in sessions 2, 5, 6, promoted after session 6.

  Three other candidates stayed candidates. `make fixtures` and `make fmt` had only 1 and 2 confirmations, because the agent mostly called the scripts directly. One candidate had 4 confirmations but was blocked by 2 contradictions.
- **Tool pruning.** After session 4, the 5 irrelevant tools (`http_get`, `sql_query`, `image_resize`, `send_email`, `translate_text`) were pruned in the controlled arm, justified by sessions 1 to 4. The prune-only arm also pruned `grep`. Sessions 5 and 6 ran without them.
- **Cost curve.** As in the paper, the controlled arm costs more early (+10% to +17% on tasks 1 to 3, mostly controller overhead) and less later (36.8k, 44.7k and 30.6k vs 45.7k, 48.1k and 47.0k tokens). Its cumulative tokens cross below baseline at **task 6**.
- **Pruning was the biggest single lever**, which matches the paper's ablation.
- **Cache-aware dollars tell a different story.** The CLI caches the system prompt and tool schemas, so most of what pruning saves is cheap cache reads, while the controller's own calls are uncached. In notional dollars, the controlled arm is still about 10% more expensive than baseline after 6 tasks. Only prune-only is cheaper on both metrics. With 6 tasks and one seed, all of these differences are within run-to-run noise.

### Loop guard: one live redirect, on Ollama only

Across the 12 Claude sessions with the guard on (controlled and guard-only), the rule trigger opened about 16 times. Every time, the Haiku judge returned `stuck: false` (confidence 0.7 to 0.9), so no redirect was injected.

Two follow-ups also produced no loop:

- I widened the trigger so its repeat counters span the whole trajectory, as in the paper. Then I ran one extra Claude session on probe task 13, which is deliberately unsatisfiable: `./bin/econ run --tasks 13 --arm guard-only --max-steps 20`. The judge again said "not stuck", and the agent noticed the contradictory tests and called `finish` after 12 steps.
- One optional local session on Ollama (`ECON_BACKEND=ollama`, qwen3:8b) called `finish` on task 1 after 4 steps without looping, but its check FAILED (it never ran `make fmt`).

The injection path itself is covered by the offline test `test_loop_guard_redirect_is_injected_and_the_turn_continues`. In that test the redirect is injected after step 3, the agent sees it as a `[controller note]` in its next prompt, and steps 4 to 7 still run. A later verification pass saw one live redirect, on Ollama: `ECON_BACKEND=ollama ./bin/econ run --tasks 13 --arm guard-only --max-steps 20` (qwen3:8b agent, qwen3:4b-instruct judge). From step 2 on, the agent's replies were not JSON actions. At step 6 the trigger fired (one failure signature repeated 5 times), the judge returned `stuck: true` at confidence 0.99, and a redirect was injected. The session then ran steps 7 to 20 without being stopped. It was still stuck until step 20 and the check failed. The log is `runs/verify-ollama-probe13/*/sessions/0001-task13.jsonl`. It ran with the two changes made in that pass: the trigger now reports every counter that fired, and the judge is skipped on the final step. **No redirect has been observed with Claude**; in that pass the Haiku judge said `stuck: true` once, at confidence 0.6, which is below the 0.7 threshold. This matches the paper, where the guard is the smallest lever and redirects happen on roughly one task in ten over much longer, harder SWE-Bench trajectories.

## Limits and honest caveats

- **Small, noisy demo.** One seed, 6 tasks and a Haiku agent. LLM runs vary a lot from session to session, so a single run's break-even index is anecdotal. It is not a measurement of the paper's claim, and the paper used three seeds and 277 tasks.
- **Toy environment.** The quirks are designed so that micro-skills have something to learn. Real repos have fewer repeated quirks per task and much larger tool catalogs; Claude Code loads 23 tools in the paper's profiling.
- **Simplified levers.** There is no need-rate gate, no gamma decay, no per-task tool-relevance judge, no automatic re-audit, and no learned turn budget or forced stop. See `PAPER_NOTES.md`.
- **Token metric.** The headline curve sums all token classes, cache reads included. The CLI's cache-aware notional dollar figure is shown next to it. On a subscription neither number is billed directly; both come from the CLI's usage report.
- **Agent shell commands.** The agent runs shell commands chosen by the model inside the seeded toy repo, with a restricted PATH and a 60s timeout. This is not a security sandbox.
- **Personal use only.** This project drives the Claude Code CLI with your personal claude.ai login. Sharing it or deploying it for other people requires switching to API-key authentication, as Anthropic's Agent SDK terms require. Do not ship it with subscription auth.
