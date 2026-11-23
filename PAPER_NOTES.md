# Paper notes: TokenEconomizer (arXiv 2610.22732)

Li et al., "TokenEconomizer: Experience-Driven Black-Box Cost Control for Coding Agents", UC Berkeley, Microsoft Research and Microsoft Security, Oct 2026. The local copy is in `paper/tokeneconomizer.pdf`.

## The mechanism, in my words

Coding agents are usually scored one task at a time. Real use is a stream of tasks in one repository. In that stream the agent keeps paying for the same things: it rediscovers how to run the tests, it carries tool schemas it never calls, and it burns turns in failure loops. Each turn re-bills the system prompt, the tool schemas and the growing history, so these costs multiply.

TokenEconomizer is an **external controller**. It never touches the model or the agent loop. It only uses what a harness already exposes: the instruction prefix, the loaded tool set, and the live event stream. Between tasks it learns a per-repository policy from the traces of completed tasks. The policy has three parts:

1. **Micro-skills.** An LLM proposer reads compact digests of finished tasks and drafts one-line operational facts, for example "tests run with this native runner". Each fact comes with executable detectors (regexes over commands and outputs). The controller, not the LLM, runs those detectors on every new trace and records whether the trace confirmed or contradicted the fact. A fact is injected into later prompts only after it has been confirmed on 3 tasks. It is retired permanently once its support turns negative, where one contradiction weighs as much as several confirmations (lambda = 5).
2. **Tool pruning.** Shell, read, write and edit are always kept. Any other tool that has gone uncalled for a fixed number of consecutive tasks is masked from the tool list. The paper adds a per-task relevance judge and restores the full catalog every 5 tasks so a wrongly pruned tool can come back.
3. **Failure-loop intervention.** After each tool call, a model-free rule looks for repeated failure signatures (the first error line, normalized), edit churn and dense errors. Only while that rule fires does a cheap LLM judge read a digest and decide "stuck or progressing". On a confident stuck verdict the controller injects one redirect that names the pattern and asks for a different approach. It does not kill the run, and in the paper it stops a rollout only after a second stuck verdict past a learned turn budget.

Controller calls (proposer, judges) are charged to the task that triggered them. Early in a stream the controller adds cost and saves little. Later it saves on every turn, so cumulative cost crosses below the default at some **break-even task**: by the sixth task in the paper's Fig. 5b. Reported savings are 18 to 31% on SWE-Bench with comparable Pass@1. Tool pruning is the largest single lever, micro-skills come next, and the loop guard is in single digits.

## Concept to code map

| Paper concept | Where it lives here |
|---|---|
| Sealed agent + harness | `econ/agent.py` `run_agent()`. It is configured only via `settings.json` (`load_settings`) and an `on_step` hook, the same way Claude Code exposes settings and hooks |
| Instruction prefix / micro-skill manual | `controller.render_manual()` -> `<state>/skills.md`, placed ahead of the task by `agent.render_prompt()` |
| Per-repo state S_i | `controller.State`: `~/.econ/<repo-hash>/{sessions/*.jsonl, candidates.json, skills.md, allowlist.json}` |
| Task digests | `controller.digest_steps()`, `controller.session_commands()` |
| LLM proposer | `controller.State.learn_skills()` + `MINER_PROMPT` (small model: Claude `haiku` by default) |
| Detector (confirm regex), evaluated by the controller | `controller.evaluate_candidate()` |
| Grounding: candidate must cite evidence | `controller.admit_proposals()` rejects a regex that matched no command that held in this session |
| Retroactive evidence on admission | `admit_proposals(..., history)` |
| Confirmation condition (3 tasks) | `MIN_CONFIRM = 3`, `controller.update_status()` |
| Support q_i(f), lambda, retirement | `update_status()`: support = confirms - 5 * contradictions, retire when < 0 (no decay) |
| Tool pruning by unused streak | `controller.compute_pruning()` (N = 4), `State.update_pruning()` -> `allowlist.json` |
| Protected core tools | `tools.PROTECTED` |
| Recovery from a wrong prune | `econ restore-tools` -> `State.restore_tools()` (streaks restart after a restore) |
| Failure signature | `controller.signature()` |
| Rule-based trigger (no model call) | `controller.rule_trigger()` |
| Trace judge + redirect writer | `controller.LoopGuard` + `JUDGE_PROMPT` (one call returns verdict and redirect) |
| Redirect, not abort | `LoopGuard.__call__` returns text and the agent appends it as a `[controller note]` and keeps looping |
| Cost accounting incl. controller overhead | `run_session()` `end` event: `agent_tokens + controller_tokens`, plus the CLI's cache-aware `cost_usd` |
| Cumulative cost curve, break-even task | `report.cumulative()`, `report.break_even()`, `report.render()` |
| Ablations (each meta-strategy alone) | `controller.ARMS`: `skills-only`, `prune-only`, `guard-only` vs `baseline` and `controlled` |
| Task stream in one repository | `econ/toyrepo.py`: 12 tasks on a seeded repo with three quirks |

## What I simplified and why

- **Benchmark.** The paper uses 277 SWE-Bench tasks with Claude Code, Codex and Copilot CLI. Here a toy repository has 12 scripted tasks, and the live demo runs 6 of them. This keeps the Pro-subscription usage small while still showing promotion (needs 3 sessions) and pruning (needs 4).
- **Agent.** The paper wraps the real Claude Code harness. Here the black box is a small ReAct loop whose LLM is Claude, called through `claude -p` with built-in tools disabled. Every turn re-sends the system prompt, the tool schemas and the history, the same billing shape as the paper's Fig. 1a. The 12 tools include 5 deliberately irrelevant ones.
- **Independent confirmation.** The paper requires confirming tasks with non-overlapping edited files. Nearly every toy task edits `core.py`, so that rule would block everything. I count distinct sessions instead.
- **Detectors.** The miner emits only a `confirm` regex. Contradiction is a fixed rule: a matching command whose output says "command not found", "No module named" and so on. I dropped the scope, need and extractor detectors, the need-rate gate, and the gamma decay.
- **Proposer cadence.** The miner runs after every session (the paper runs it after task 4 and then every 5 tasks). With 6 tasks the paper's cadence would never fire twice.
- **Pruning.** I dropped the per-task LLM relevance judge (its saving overlaps with the streak rule, and it adds a call per task). I also dropped the automatic re-audit every 5 tasks, which is replaced by the explicit `econ restore-tools`.
- **Loop guard.** It is checked every K = 3 steps rather than after every call, and the paper's turn budget B_i and forced stop are not implemented. There is at most 1 redirect per session and the guard never terminates a session, as the brief asks. The judge's verdict and the redirect text come from one call instead of two, to save usage. The rule trigger first looked only at the last 6 steps, which is the version used in the 6-task report run. After that run I changed it to count repeated failure signatures and repeated failing actions over the whole trajectory, as the paper does; the probe session in the README used the new version. A verification pass then made the trigger return every reason that fired, not just the first one, so that the judge sees all the running counters. It also stopped consulting the judge on a session's final step, where a redirect could never be used.
- **Cost metric.** The headline curve sums all token classes (uncached input, cache write, cache read, output), like Ollama's `prompt_eval_count + eval_count`. The CLI's cache-aware notional dollar cost is reported next to it, because cache reads are cheap and the paper weights them.
