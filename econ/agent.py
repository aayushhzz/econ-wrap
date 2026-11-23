"""A small ReAct coding agent. The controller treats it as a black box.

The only surfaces the controller touches are the ones a real harness exposes:
  * settings.json  - {"tools": [...allowed tool names], "manual": "...text placed before the task..."}
  * on_step hook   - called after every tool call with the history; a returned string is appended
                     to the conversation as a user message (like a hook's additionalContext).
The agent loop itself, its prompt format and its tools are never modified by the controller.
"""
import json

from . import tools as toolmod

SYSTEM = """You are an autonomous coding agent working inside one Python repository (the current directory).
Solve the task by calling tools, one tool call per reply.
Reply with ONLY a JSON object: {{"thought": "<one short sentence>", "tool": "<tool name>", "args": {{...}}}}
Call the finish tool once the task's tests pass.

Available tools (JSON schemas):
{schemas}"""

FULL_STEPS = 4  # older steps are shown with a clipped result to bound prompt growth


def load_settings(path):
    with open(path) as f:
        s = json.load(f)
    return {"tools": [t for t in s.get("tools", toolmod.ALL_TOOLS) if t in toolmod.CATALOG],
            "manual": s.get("manual", "")}


def parse_action(text):
    """First JSON object in the reply that names a tool, or None."""
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(text[i:])
            if isinstance(obj, dict) and "tool" in obj:
                return obj
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return None


def render_prompt(task, manual, history):
    parts = []
    if manual:
        parts.append(manual)
    parts.append("TASK:\n" + task)
    n_steps = sum(1 for h in history if h["type"] == "step")
    seen = 0
    for h in history:
        if h["type"] == "inject":
            parts.append("[controller note] " + h["text"])
            continue
        seen += 1
        out = h["output"]
        if n_steps - seen >= FULL_STEPS and len(out) > 200:
            out = out[:200] + " ...[clipped]"
        parts.append("STEP %d ACTION: %s\nRESULT (%s): %s" % (
            h["step"], json.dumps({"tool": h["tool"], "args": h["args"]}), "ok" if h["ok"] else "failed", out))
    parts.append("Your next action (one JSON object):")
    return "\n\n".join(parts)


def run_agent(task, workdir, settings, backend, on_step=None, log=None, max_steps=12):
    """Run until finish or max_steps. Returns {"steps", "finished", "history", "tokens"}."""
    log = log or (lambda event: None)
    allowed = settings["tools"]
    system = SYSTEM.format(schemas=toolmod.render_schemas(allowed))
    history = []
    tokens = 0
    finished = False
    step = 0
    for step in range(1, max_steps + 1):
        reply = backend.complete(system, render_prompt(task, settings.get("manual", ""), history), role="agent")
        tokens += reply.tokens
        log({"type": "llm", "who": "agent", "step": step, "usage": reply.usage, "tokens": reply.tokens})
        action = parse_action(reply.text)
        if action is None:
            name, args, ok, out = "(none)", {}, False, "error: reply was not a JSON action object"
        else:
            name, args = str(action.get("tool")), action.get("args") or {}
            if name not in allowed:
                ok, out = False, "error: tool %r is not available" % name
            elif name == "finish":
                ok, out = True, "finished"
            else:
                ok, out = toolmod.execute(workdir, name, args)
        h = {"type": "step", "step": step, "tool": name, "args": args, "ok": ok, "output": out}
        history.append(h)
        log(dict(h))
        if name == "finish" and ok:
            finished = True
            break
        if on_step:
            note = on_step(step, history)
            if note:
                history.append({"type": "inject", "step": step, "text": note})
                log({"type": "inject", "step": step, "text": note})
    return {"steps": step, "finished": finished, "history": history, "tokens": tokens}
