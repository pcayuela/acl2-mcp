#!/usr/bin/env python3
"""
acl2_mcp_server.py

A single MCP server that drives either ACL2 or ACL2(r) as a persistent
interactive subprocess over a real pseudo-terminal (via pexpect), instead
of piping stdin/stdout. Piping the live process (e.g. into `head`/`grep`)
is what causes the SBCL "broken pipe" / nested-error crash seen when
driving ACL2 from a plain shell pipeline; a pty behaves like a real
terminal and avoids that class of failure entirely.

Which executable this instance drives is configured either via CLI args:

    --acl2-path /home/you/acl2/saved_acl2      (or saved_acl2r)
    --label "ACL2"                             (or "ACL2(r)")

or via environment variables as a fallback:

    ACL2_EXECUTABLE=/home/you/acl2/saved_acl2
    ACL2_LABEL=ACL2

Prefer the CLI-arg form when this is launched by Claude Desktop on
Windows through `wsl.exe` - env vars set in the Desktop app's own MCP
config do not automatically cross the Windows/WSL boundary, but
command-line arguments do.
"""

import argparse
import os
import re
import sys
import threading

import pexpect
from mcp.server.fastmcp import FastMCP

# --- configuration -----------------------------------------------------

_parser = argparse.ArgumentParser()
_parser.add_argument("--acl2-path", default=None)
_parser.add_argument("--label", default=None)
_args, _unknown = _parser.parse_known_args()

ACL2_PATH = _args.acl2_path or os.environ.get("ACL2_EXECUTABLE")
LABEL = _args.label or os.environ.get("ACL2_LABEL", "ACL2")

if not ACL2_PATH:
    print(
        "ERROR: no ACL2 executable configured. Pass --acl2-path /path/to/saved_acl2"
        " or set the ACL2_EXECUTABLE environment variable.",
        file=sys.stderr,
    )
    sys.exit(1)

if not os.path.isfile(ACL2_PATH) or not os.access(ACL2_PATH, os.X_OK):
    print(f"ERROR: {ACL2_PATH!r} does not exist or is not executable.", file=sys.stderr)
    sys.exit(1)

# Matches the top-level ACL2 prompt, e.g. "ACL2 !>" or "ACL2(r) !>".
PROMPT_RE = re.compile(r"ACL2(?:\(r\))?\s*[a-zA-Z]*!>\s*$")

# A raw Lisp/SBCL debugger break prompt looks like "0] " (incrementing per
# nesting level) at the start of a line - this means something dropped
# below the ACL2 level. Scripted recovery from an arbitrary nested Lisp
# break is not reliable, so we surface it and recommend reset() instead.
DEBUGGER_RE = re.compile(r"^\d+\]\s*$", re.MULTILINE)

_lock = threading.Lock()
_child = None  # pexpect.spawn instance, or None if never started


def _spawn():
    global _child
    child = pexpect.spawn(
        ACL2_PATH,
        encoding="utf-8",
        codec_errors="replace",
        timeout=120,
        cwd=os.path.expanduser("~"),
    )
    child.expect(PROMPT_RE, timeout=120)
    _child = child
    return child


def _ensure_alive():
    global _child
    if _child is None or not _child.isalive():
        _spawn()
    return _child


def _wait_for_prompt(child, timeout_seconds: int) -> str:
    try:
        idx = child.expect([PROMPT_RE, DEBUGGER_RE, pexpect.EOF], timeout=timeout_seconds)
    except pexpect.TIMEOUT:
        return (
            f"[TIMEOUT after {timeout_seconds}s - still running. Output so far:]\n"
            f"{child.before}\n"
            "[Call wait() to keep waiting, or reset() to give up on it.]"
        )
    output = (child.before or "") + (child.after or "")
    if idx == 0:
        return output
    if idx == 1:
        return (
            "[WARNING: dropped into the raw Lisp/SBCL debugger, not the normal "
            "ACL2 prompt. Do not send more forms - call reset() to recover.]\n"
            + output
        )
    return (
        "[The ACL2 process exited unexpectedly. Call reset() to start a new "
        "session.]\n" + output
    )


mcp = FastMCP(f"{LABEL}-mcp")


@mcp.tool()
def submit(form: str, timeout_seconds: int = 120) -> str:
    """Submit one or more Lisp forms (e.g. defthm, defun, include-book) to
    the running ACL2/ACL2(r) session and return everything it prints up to
    the next top-level prompt. Starts a fresh session automatically if one
    isn't already running. If the result doesn't end with a normal ACL2
    prompt (for example it dropped into the raw Lisp debugger), call
    reset() rather than sending more forms."""
    with _lock:
        child = _ensure_alive()
        child.sendline(form)
        return _wait_for_prompt(child, timeout_seconds)


@mcp.tool()
def wait(timeout_seconds: int = 120) -> str:
    """Keep waiting for the current computation to finish without sending
    any new input. Use this after a submit() call timed out on a
    long-running proof, to pick up where it left off."""
    with _lock:
        if _child is None or not _child.isalive():
            return "No session is running. Call submit() to start one."
        return _wait_for_prompt(_child, timeout_seconds)


@mcp.tool()
def reset() -> str:
    """Kill the current ACL2/ACL2(r) process (if any) and start a fresh
    one. Use this to recover from a raw Lisp debugger break, a hang, or
    any state you're not confident is clean."""
    global _child
    with _lock:
        if _child is not None and _child.isalive():
            try:
                _child.close(force=True)
            except Exception:
                pass
        _child = None
        _spawn()
        return f"{LABEL} session restarted."


@mcp.tool()
def status() -> str:
    """Report whether the ACL2/ACL2(r) session is currently running."""
    with _lock:
        if _child is None:
            return f"{LABEL}: not started yet."
        if _child.isalive():
            return f"{LABEL}: running (pid {_child.pid})."
        return f"{LABEL}: process exited."


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
