#!/usr/bin/env python3
"""Quick standalone check that pexpect can drive a given ACL2/ACL2(r)
executable over a pty: spawn it, wait for the banner/prompt, submit a
trivial form, and confirm we get a response back - all without going
through the MCP protocol, to isolate problems."""
import re
import sys

import pexpect

PROMPT_RE = re.compile(r"ACL2(?:\(r\))?\s*[a-zA-Z]*!>\s*$")


def main():
    if len(sys.argv) != 2:
        print("Usage: smoke_test.py /path/to/saved_acl2[r]", file=sys.stderr)
        sys.exit(2)
    path = sys.argv[1]
    print(f"Spawning {path} ...")
    child = pexpect.spawn(path, encoding="utf-8", codec_errors="replace", timeout=60)
    child.expect(PROMPT_RE)
    banner_lines = [l for l in child.before.splitlines() if "ACL2 Version" in l]
    print("Banner:", banner_lines[0].strip() if banner_lines else "(not found)")
    child.sendline("(+ 1 2)")
    child.expect(PROMPT_RE)
    print("Sent (+ 1 2), session replied:")
    print(child.before.strip())
    child.close(force=True)
    print(f"OK - pexpect/pty control works for {path}")


if __name__ == "__main__":
    main()
