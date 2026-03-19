#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


AUTH_KEYS = {
    "youtube": [
        "YTDLP_COOKIE_FILE",
        "YTDLP_COOKIES_B64",
        "YTDLP_VISITOR_DATA",
        "YTDLP_PO_TOKEN_WEB",
        "YTDLP_PO_TOKEN_ANDROID",
    ],
    "instagram": [
        "INSTAGRAM_COOKIE_FILE",
        "INSTAGRAM_COOKIES_B64",
    ],
    "tiktok": [
        "TIKTOK_COOKIE_FILE",
        "TIKTOK_COOKIES_B64",
    ],
}


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


def _selected_keys(platform: str) -> list[str]:
    if platform == "all":
        return AUTH_KEYS["youtube"] + AUTH_KEYS["instagram"]
    return AUTH_KEYS[platform]


def _show_status(lines: list[str], keys: list[str]) -> None:
    current: dict[str, str] = {}
    for line in lines:
        for key in keys:
            match = re.match(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=\s*(.*)\s*$", line)
            if match:
                current[key] = match.group(1)
    for key in keys:
        raw = current.get(key)
        if raw is None:
            print(f"{key}=<unset>")
            continue
        value = raw.strip().strip("'").strip('"')
        if key.endswith("_FILE"):
            path = Path(value)
            exists = path.exists()
            suffix = f" exists={exists}"
            if exists:
                suffix += f" size={path.stat().st_size}"
            print(f"{key}={value}{suffix}")
        else:
            print(f"{key}={_mask(value)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage YouTube/Instagram auth env vars for CrediClip worker."
    )
    parser.add_argument(
        "--platform",
        choices=["youtube", "instagram", "tiktok", "all"],
        default="all",
        help="Which platform auth keys to manage.",
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
        help="Upsert selected auth keys from current process environment.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove selected auth keys from env file.",
    )
    parser.add_argument(
        "--show-status",
        action="store_true",
        help="Print current selected auth key status.",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Restart worker service after file update.",
    )
    args = parser.parse_args()

    if not args.set_from_env and not args.clear and not args.show_status:
        parser.error("Specify at least one of: --set-from-env, --clear, --show-status")

    if os.geteuid() != 0:
        print("Run this script as root (use sudo).", file=sys.stderr)
        return 1

    env_path = Path(args.env_file)
    lines = _parse_env_lines(env_path.read_text(encoding="utf-8")) if env_path.exists() else []
    keys = _selected_keys(args.platform)
    changed = False

    if args.show_status:
        _show_status(lines, keys)

    if args.clear:
        for key in keys:
            lines, removed = _remove_key(lines, key)
            if removed:
                changed = True
                print(f"removed {key}")

    if args.set_from_env:
        updates = {k: (os.getenv(k) or "").strip() for k in keys}
        updates = {k: v for k, v in updates.items() if v}
        if not updates:
            print(
                "No selected auth variables found in current environment. "
                "Export one or more of: " + ", ".join(keys),
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

    if args.restart:
        _restart_service(args.service_name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
