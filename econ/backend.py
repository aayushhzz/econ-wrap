"""One small interface for every model call: complete(system, prompt, role) -> Reply.

role is "agent" (the coding agent) or "small" (miner and loop-guard judge).

Backends:
  claude  (default) - spawns the locally logged-in `claude -p --output-format json`, so calls run
                      on the user's Claude subscription. No API key is read or set here.
  ollama            - local Ollama HTTP API (qwen3:8b agent, qwen3:4b-instruct small).
  fake              - scripted, offline; used by tests.
"""
import json
import os
import subprocess
import tempfile
import urllib.request


class BackendError(RuntimeError):
    pass


class Reply(object):
    def __init__(self, text, usage):
        self.text = text
        # token classes: input (uncached), cache_write, cache_read, output
        self.usage = usage

    @property
    def tokens(self):
        return sum(self.usage.get(k, 0) for k in ("input", "cache_write", "cache_read", "output"))


class ClaudeCLIBackend(object):
    """Each call is a fresh, tool-less `claude -p` with our own system prompt.

    Flags keep the call small and side-effect free: no built-in tools, no user/project settings
    (so no hooks or CLAUDE.md), no MCP servers, no session files.
    """

    def __init__(self, model=None, small_model="haiku", binary="claude", timeout=300):
        self.model = model  # None = the CLI's default model
        self.small_model = small_model
        self.binary = binary
        self.timeout = timeout
        self._cwd = tempfile.mkdtemp(prefix="econ-claude-")  # empty dir: nothing to auto-discover

    def complete(self, system, prompt, role="agent"):
        model = self.model if role == "agent" else self.small_model
        cmd = [self.binary, "-p", "--output-format", "json", "--system-prompt", system,
               "--tools", "", "--setting-sources", "", "--settings", '{"disableAllHooks": true}',
               "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"]
        if model:
            cmd += ["--model", model]
        last = None
        for _ in range(2):  # one retry for transient CLI/network failures
            try:
                proc = subprocess.run(cmd, input=prompt.encode("utf-8"), stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, cwd=self._cwd, timeout=self.timeout)
                data = json.loads(proc.stdout.decode("utf-8"))
            except (subprocess.TimeoutExpired, ValueError) as e:
                last = e
                continue
            if data.get("is_error"):
                last = data.get("result") or data.get("subtype")
                continue
            u = data.get("usage") or {}
            return Reply(data.get("result") or "", {
                "input": u.get("input_tokens", 0), "cache_write": u.get("cache_creation_input_tokens", 0),
                "cache_read": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0),
                "cost_usd": data.get("total_cost_usd", 0.0)})
        raise BackendError("claude CLI call failed: %s" % (last,))


class OllamaBackend(object):
    def __init__(self, model="qwen3:8b", small_model="qwen3:4b-instruct", url="http://localhost:11434"):
        self.model, self.small_model, self.url = model, small_model, url

    def complete(self, system, prompt, role="agent"):
        body = {"model": self.model if role == "agent" else self.small_model, "stream": False, "think": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "options": {"temperature": 0.2}}
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = json.loads(r.read().decode())
        except OSError as e:
            raise BackendError("ollama call failed: %s" % e)
        return Reply(data["message"]["content"],
                     {"input": data.get("prompt_eval_count", 0), "output": data.get("eval_count", 0)})


class FakeBackend(object):
    """fn(system, prompt, role) -> str. Usage is approximated as chars/4 so cost curves still work."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def complete(self, system, prompt, role="agent"):
        self.calls.append((system, prompt, role))
        text = self.fn(system, prompt, role)
        return Reply(text, {"input": (len(system) + len(prompt)) // 4, "output": len(text) // 4})


def get_backend(name=None):
    name = name or os.environ.get("ECON_BACKEND", "claude")
    if name == "claude":
        return ClaudeCLIBackend(model=os.environ.get("ECON_MODEL") or None,
                                small_model=os.environ.get("ECON_SMALL_MODEL", "haiku"))
    if name == "ollama":
        return OllamaBackend(model=os.environ.get("ECON_MODEL", "qwen3:8b"),
                             small_model=os.environ.get("ECON_SMALL_MODEL", "qwen3:4b-instruct"))
    raise BackendError("unknown ECON_BACKEND %r (use claude or ollama)" % name)
