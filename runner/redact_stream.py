#!/usr/bin/env python3
"""Copy stdin to stdout, replacing secret values with [redacted].

Values come from the env vars named in CIRCUIT_REDACT (default
OPENROUTER_API_KEY, 8+ characters), never argv, so they don't show in ps.
The replace is literal, line by line: a secret split across lines is not
caught (spec §4.3).
"""
import os
import sys

names = [n.strip() for n in os.environ.get("CIRCUIT_REDACT", "OPENROUTER_API_KEY").split(",")]
secrets = [os.environ[n].encode() for n in names if n and len(os.environ.get(n, "")) >= 8]
for line in sys.stdin.buffer:
    for secret in secrets:
        line = line.replace(secret, b"[redacted]")
    sys.stdout.buffer.write(line)
    sys.stdout.buffer.flush()
