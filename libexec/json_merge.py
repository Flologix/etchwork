#!/usr/bin/env python3
"""JSON config editor for `etchwork init` (etchwork extraction design §4.1).

Subcommands:
  mcp <file> <project> <uid> <gid> <image> [--flai]
    Sets mcpServers.etchwork to the docker-run entry that mounts <project> at
    /workspace, as <uid>:<gid>, using <image>. With --flai, adds
    "shared": false (flai's convention for a server it manages itself).
    Removes mcpServers.circuit, but only when its args contain a string
    starting "flai-circuit:" -- a user's own "circuit" server that isn't the
    old engine is left alone. Prints "removed legacy circuit server" when it
    does remove one.
  allow <file> <pattern>
    Adds <pattern> to permissions.allow (creating permissions/allow as
    needed), once, and keeps every other key untouched.
  validate <file> [<file> ...]
    Checks that each file is either missing or valid JSON, without writing
    anything. Used to check every file a command is about to touch before it
    writes any of them (init.sh's claude-code path checks .mcp.json and
    .claude/settings.json this way before creating .claude/skills/etchwork).

Every subcommand: a missing file is treated as {}; a file that exists but
isn't valid JSON -- or parses but isn't the object shape this tool expects
(top level, mcpServers, or permissions.allow of the wrong type) -- is left
untouched, and reported as a one-line error, not a traceback.
"""
import json
import sys


def fatal(path):
    sys.stderr.write(f"{path} is not valid JSON; fix or remove it, then re-run\n")
    sys.exit(1)


def load(path):
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        fatal(path)
    if not isinstance(data, dict):
        fatal(path)
    return data


def save(path, data):
    with open(path, "w") as f:
        f.write(json.dumps(data, indent=2) + "\n")


def cmd_validate(argv):
    for path in argv:
        load(path)


def cmd_mcp(argv):
    file, project, uid, gid, image = argv[0], argv[1], argv[2], argv[3], argv[4]
    flai = "--flai" in argv[5:]

    root = load(file)
    servers = root.get("mcpServers", {})
    if not isinstance(servers, dict):
        fatal(file)
    root["mcpServers"] = servers

    entry = {
        "command": "docker",
        "args": [
            "run", "-i", "--rm", "--pull", "never", "--network", "none",
            "--user", f"{uid}:{gid}",
            "-v", f"{project}:/workspace",
            image,
        ],
    }
    if flai:
        entry["shared"] = False
    servers["etchwork"] = entry

    legacy = servers.get("circuit")
    if isinstance(legacy, dict) and any(
        isinstance(a, str) and a.startswith("flai-circuit:") for a in legacy.get("args", [])
    ):
        del servers["circuit"]
        print("removed legacy circuit server")

    save(file, root)


def cmd_allow(argv):
    file, pattern = argv[0], argv[1]
    root = load(file)
    permissions = root.get("permissions", {})
    if not isinstance(permissions, dict):
        fatal(file)
    root["permissions"] = permissions

    allow = permissions.get("allow", [])
    if not isinstance(allow, list):
        fatal(file)
    permissions["allow"] = allow

    if pattern not in allow:
        allow.append(pattern)
    save(file, root)


def main(argv):
    if not argv:
        sys.stderr.write("usage: json_merge.py mcp|allow|validate ...\n")
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "mcp":
        cmd_mcp(rest)
    elif cmd == "allow":
        cmd_allow(rest)
    elif cmd == "validate":
        cmd_validate(rest)
    else:
        sys.stderr.write(f"unknown subcommand: {cmd}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
