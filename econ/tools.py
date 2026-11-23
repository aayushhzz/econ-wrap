"""The agent's 12-tool catalog: schemas (what the model sees) and implementations.

Several tools are deliberately irrelevant to a local bug-fix repo (http_get, sql_query,
image_resize, send_email, translate_text). Their schemas still cost tokens on every
turn, which is what tool pruning removes.
"""
import json
import os
import re
import subprocess
import sys

OUTPUT_LIMIT = 1500

# name -> (description, {param: description})
CATALOG = {
    "read_file": ("Read a UTF-8 text file from the repository and return its contents.",
                  {"path": "Path relative to the repository root."}),
    "write_file": ("Create or overwrite a text file in the repository with the given content.",
                   {"path": "Path relative to the repository root.", "content": "Full new file content."}),
    "replace_in_file": ("Replace one exact occurrence of a string in a file. Fails if the old string is missing.",
                        {"path": "Path relative to the repository root.", "old": "Exact text to replace.",
                         "new": "Replacement text."}),
    "run_shell": ("Run a shell command in the repository root (60s timeout). Returns exit code and output tail.",
                  {"command": "The shell command line to run, e.g. 'python -m pytest -q'."}),
    "list_dir": ("List files and directories under a path in the repository (recursive, up to 200 entries).",
                 {"path": "Directory relative to the repository root; '.' for the root."}),
    "grep": ("Search repository files for a regular expression and return matching lines with file:line prefixes.",
             {"pattern": "Python regular expression.", "path": "Directory or file to search; '.' for everything."}),
    "http_get": ("Fetch a URL over HTTP(S) and return the response body. Useful for reading web documentation, "
                 "REST APIs and remote JSON resources.",
                 {"url": "Absolute http(s) URL.", "headers": "Optional JSON object of request headers."}),
    "sql_query": ("Execute a SQL statement against the project's configured relational database and return rows "
                  "as JSON. Supports SELECT, INSERT, UPDATE and DDL statements.",
                  {"query": "SQL statement.", "database": "Optional database name; defaults to the primary DB."}),
    "image_resize": ("Resize or convert an image file (PNG, JPEG, WebP, GIF), optionally preserving the aspect "
                     "ratio, and write the result to a new path.",
                     {"path": "Input image path.", "width": "Target width in pixels.",
                      "height": "Target height in pixels.", "output": "Output image path."}),
    "send_email": ("Send an email notification to one or more recipients, with optional CC and a plain-text body. "
                   "Use for alerting maintainers about results.",
                   {"to": "Comma-separated recipient addresses.", "subject": "Subject line.",
                    "body": "Plain-text body."}),
    "translate_text": ("Translate text between natural languages using a machine translation service and return "
                       "the translated string.",
                       {"text": "Text to translate.", "target_language": "ISO 639-1 code, e.g. 'fr'."}),
    "finish": ("Declare the task complete once the requested tests pass. Ends the session.",
               {"summary": "One sentence describing what you changed."}),
}

ALL_TOOLS = list(CATALOG)
# Paper: core tools for shell, read, write and edit are never pruned.
PROTECTED = {"read_file", "write_file", "replace_in_file", "run_shell", "finish"}


def render_schemas(names):
    """The tool section of the system prompt. Only `names` are shown to the model."""
    lines = []
    for name in names:
        desc, params = CATALOG[name]
        schema = {"name": name, "description": desc,
                  "parameters": {"type": "object",
                                 "properties": {p: {"type": "string", "description": d} for p, d in params.items()}}}
        lines.append(json.dumps(schema))
    return "\n".join(lines)


def _safe_path(root, rel):
    full = os.path.realpath(os.path.join(root, rel or "."))
    base = os.path.realpath(root)
    if full != base and not full.startswith(base + os.sep):
        raise ValueError("path escapes the repository: %s" % rel)
    return full


def _clip(text):
    if len(text) <= OUTPUT_LIMIT:
        return text
    return "...[truncated]...\n" + text[-OUTPUT_LIMIT:]


def shell_env():
    """Clean env for agent commands: the venv's bin first (python/pytest), no PYTHONPATH, no black."""
    venv_bin = os.path.dirname(sys.executable)
    return {"PATH": venv_bin + ":/usr/bin:/bin:/usr/sbin:/sbin", "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}


def execute(root, name, args):
    """Run one tool call. Returns (ok, output). Never raises for bad model input."""
    try:
        args = args if isinstance(args, dict) else {}
        if name == "read_file":
            with open(_safe_path(root, args.get("path")), encoding="utf-8") as f:
                return True, _clip(f.read())
        if name == "write_file":
            path = _safe_path(root, args.get("path"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(args.get("content", "")))
            return True, "wrote %s" % args.get("path")
        if name == "replace_in_file":
            path = _safe_path(root, args.get("path"))
            with open(path, encoding="utf-8") as f:
                text = f.read()
            old = str(args.get("old", ""))
            if not old or old not in text:
                return False, "error: old string not found in %s" % args.get("path")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace(old, str(args.get("new", "")), 1))
            return True, "replaced 1 occurrence in %s" % args.get("path")
        if name == "run_shell":
            proc = subprocess.run(str(args.get("command", "")), shell=True, cwd=root, env=shell_env(),
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            out = proc.stdout.decode("utf-8", "replace")
            return proc.returncode == 0, "exit=%d\n%s" % (proc.returncode, _clip(out))
        if name == "list_dir":
            base = _safe_path(root, args.get("path", "."))
            entries = []
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "__pycache__")
                for fn in sorted(filenames):
                    entries.append(os.path.relpath(os.path.join(dirpath, fn), root))
            return True, "\n".join(entries[:200])
        if name == "grep":
            rx = re.compile(str(args.get("pattern", "")))
            base = _safe_path(root, args.get("path", "."))
            files = [base] if os.path.isfile(base) else [
                os.path.join(d, f) for d, ds, fs in os.walk(base) for f in fs if not d.split(os.sep)[-1].startswith(".")]
            hits = []
            for fp in sorted(files):
                try:
                    with open(fp, encoding="utf-8") as f:
                        for i, line in enumerate(f, 1):
                            if rx.search(line):
                                hits.append("%s:%d:%s" % (os.path.relpath(fp, root), i, line.rstrip()))
                except (UnicodeDecodeError, OSError):
                    continue
            return True, _clip("\n".join(hits) or "(no matches)")
        if name == "http_get":
            return False, "error: network access is disabled in this sandbox"
        if name == "sql_query":
            return False, "error: no database is configured for this project"
        if name == "image_resize":
            return False, "error: input is not an image file"
        if name == "send_email":
            return False, "error: outbound email is disabled in this sandbox"
        if name == "translate_text":
            return False, "error: translation service unavailable"
        return False, "error: unknown tool %r" % name
    except subprocess.TimeoutExpired:
        return False, "error: command timed out after 60s"
    except Exception as e:  # bad paths, bad regex, missing files: report back to the model
        return False, "error: %s: %s" % (type(e).__name__, e)
