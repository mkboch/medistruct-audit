"""Executed INSIDE the sandbox by test_sandbox_mcp.py.

argv: refs_dir allowed_receipts_dir other_case_receipts_dir
Every probe except 'allowed_case_receipts' should print 'denied:...'.
"""
import os
import socket
import sys
from pathlib import Path

refs, allowed, other = sys.argv[1:4]
home = os.environ.get("PROBE_REAL_HOME", "")


def tryread(p):
    try:
        open(p, "rb").read(1)
        return "READABLE"
    except Exception as e:
        return "denied:" + type(e).__name__


def trylist(p):
    try:
        os.listdir(p)
        return "LISTABLE"
    except Exception as e:
        return "denied:" + type(e).__name__


def tryconnect(h, port):
    try:
        socket.create_connection((h, port), timeout=3)
        return "ALLOWED"
    except Exception as e:
        return "denied:" + type(e).__name__


print("home_dir", trylist(home) if home else "skipped")
print("ssh_dir", trylist(str(Path(home) / ".ssh")) if home else "skipped")
print("other_case_receipts", trylist(other))
print("reference_answers", trylist(refs))
print("allowed_case_receipts", trylist(allowed))
print("tcp_external", tryconnect("1.1.1.1", 80))
print("tcp_localhost", tryconnect("127.0.0.1", 22))
try:
    open(str(Path(other) / "x_should_fail"), "w")
    print("write_other_case ALLOWED")
except Exception as e:
    print("write_other_case denied:" + type(e).__name__)
print("env_keys", sorted(os.environ))
