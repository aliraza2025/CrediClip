#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


REMOTE_PATHS = {
    "youtube": "/home/ubuntu/youtube_cookies.txt",
    "instagram": "/home/ubuntu/instagram_cookies.txt",
    "tiktok": "/home/ubuntu/tiktok_cookies.txt",
}

ENV_KEYS = {
    "youtube": "YTDLP_COOKIE_FILE",
    "instagram": "INSTAGRAM_COOKIE_FILE",
    "tiktok": "TIKTOK_COOKIE_FILE",
}


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload a Netscape cookie file to the Oracle worker and activate it."
    )
    parser.add_argument(
        "--platform",
        choices=["youtube", "instagram", "tiktok"],
        required=True,
        help="Which worker cookie slot to update.",
    )
    parser.add_argument(
        "--cookie-file",
        required=True,
        help="Local path to a Netscape-format cookie export.",
    )
    parser.add_argument(
        "--host",
        default="ubuntu@163.192.0.221",
        help="SSH host for the Oracle worker.",
    )
    parser.add_argument(
        "--ssh-key",
        default="oracle/ssh-key-2026-02-28.key",
        help="SSH private key path.",
    )
    parser.add_argument(
        "--remote-manage-script",
        default="/home/ubuntu/CrediClip/manage_worker_platform_auth.py",
        help="Remote auth-management script path.",
    )
    parser.add_argument(
        "--show-only",
        action="store_true",
        help="Do not upload or restart; only print current remote status.",
    )
    args = parser.parse_args()

    ssh_key = Path(args.ssh_key).expanduser().resolve()
    cookie_file = Path(args.cookie_file).expanduser().resolve()
    if not ssh_key.exists():
        print(f"SSH key not found: {ssh_key}", file=sys.stderr)
        return 1
    if not args.show_only and not cookie_file.exists():
        print(f"Cookie file not found: {cookie_file}", file=sys.stderr)
        return 1

    remote_cookie_path = REMOTE_PATHS[args.platform]
    env_key = ENV_KEYS[args.platform]

    ssh_base = ["ssh", "-i", str(ssh_key), args.host]
    scp_base = ["scp", "-i", str(ssh_key)]

    if not args.show_only:
        _run(scp_base + [str(cookie_file), f"{args.host}:{remote_cookie_path}"])
        remote_cmd = (
            f"sudo chmod 600 {shlex.quote(remote_cookie_path)} && "
            f"sudo chown ubuntu:ubuntu {shlex.quote(remote_cookie_path)} && "
            f"export {env_key}={shlex.quote(remote_cookie_path)} && "
            f"sudo -E python3 {shlex.quote(args.remote_manage_script)} "
            f"--platform {args.platform} --set-from-env --restart"
        )
        _run(ssh_base + [remote_cmd])

    _run(
        ssh_base
        + [
            f"sudo python3 {shlex.quote(args.remote_manage_script)} "
            f"--platform {args.platform} --show-status"
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
