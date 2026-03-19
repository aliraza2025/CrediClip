#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


YOUTUBE_AUTH_KEYS = [
    "YTDLP_COOKIE_FILE",
    "YTDLP_COOKIES_B64",
    "YTDLP_VISITOR_DATA",
    "YTDLP_PO_TOKEN_WEB",
    "YTDLP_PO_TOKEN_ANDROID",
]


def _mask(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]} (len={len(value)})"


def _parse_env_lines(text: str) -> list[str]:
    return text.splitlines()


def _upsert_key(lines: list[str], key: str, value: str) -> tuple[list[str], bool]:
    out: list[str] = []
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    replaced = False
    rendered = f"{key}={shlex.quote(value)}"
    for line in lines:
        if pattern.match(line):
            out.append(rendered)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(rendered)
    return out, replaced


def _remove_key(lines: list[str], key: str) -> tuple[list[str], bool]:
    out: list[str] = []
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    removed = False
    for line in lines:
        if pattern.match(line):
            removed = True
            continue
        out.append(line)
    return out, removed


def _restart_service(service_name: str) -> None:
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "restart", service_name], check=True)
    subprocess.run(["systemctl", "--no-pager", "status", service_name], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage YouTube auth env vars for CrediClip worker."
    )
    parser.add_argument(
        "--env-file",
        default="/etc/default/crediclip-worker",
        help="Path to worker env file.",
    )
    parser.add_argument(
        "--service-name",
        default="crediclip-worker",
        help="systemd service name to restart when --restart is set.",
    )
    parser.add_argument(
        "--set-from-env",
        action="store_true",
        help="Upsert auth keys from current process environment.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove all YouTube auth keys from env file.",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Restart worker service after file update.",
    )
    args = parser.parse_args()

    if not args.set_from_env and not args.clear:
        parser.error("Specify at least one of: --set-from-env, --clear")

    if os.geteuid() != 0:
        print("Run this script as root (use sudo).", file=sys.stderr)
        return 1

    env_path = Path(args.env_file)
    if env_path.exists():
        lines = _parse_env_lines(env_path.read_text(encoding="utf-8"))
    else:
        lines = []

    changed = False

    if args.clear:
        for key in YOUTUBE_AUTH_KEYS:
            lines, removed = _remove_key(lines, key)
            if removed:
                changed = True
                print(f"removed {key}")

    if args.set_from_env:
        updates = {k: (os.getenv(k) or "").strip() for k in YOUTUBE_AUTH_KEYS}
        updates = {k: v for k, v in updates.items() if v}
        if not updates:
            print(
                "No YouTube auth variables found in current environment. "
                "Export one or more of: " + ", ".join(YOUTUBE_AUTH_KEYS),
                file=sys.stderr,
            )
            return 2
        for key, value in updates.items():
            lines, replaced = _upsert_key(lines, key, value)
            changed = True
            action = "updated" if replaced else "added"
            print(f"{action} {key}={_mask(value)}")

    if changed:
        content = "\n".join(lines).rstrip() + "\n"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(content, encoding="utf-8")
        print(f"wrote {env_path}")
    else:
        print("no env changes")

    if args.restart:
        _restart_service(args.service_name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
