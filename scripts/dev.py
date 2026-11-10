#!/usr/bin/env python3
"""Start the local Conversationalist voice worker and web UI together."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
WEB_ENV_KEYS = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "CONVERSATIONALIST_ACCESS_TOKEN",
    "CONVERSATIONALIST_LOCAL_AUTO_LOGIN",
)
WEB_SYSTEM_ENV_KEYS = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TMP", "TEMP",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM", "CI",
)
WEB_URL = "http://localhost:3000"


def _effective_values() -> dict[str, str]:
    """Resolve values with shell > web local > worker local precedence."""
    worker = dotenv_values(ROOT / ".env.local")
    web = dotenv_values(ROOT / "web" / ".env.local")
    result: dict[str, str] = {}
    for key in WEB_ENV_KEYS:
        value = os.environ.get(key)
        if value is None:
            value = web.get(key)
        if value is None:
            value = worker.get(key)
        if value is not None:
            result[key] = str(value)
    return result


def _web_environment() -> dict[str, str]:
    # Do not pass worker-only provider, MCP, or Hermes credentials to Next.
    env = {key: value for key, value in os.environ.items()
           if key in WEB_SYSTEM_ENV_KEYS or key.startswith("LC_")}
    env.update(_effective_values())
    return env


def _stop(processes: list[subprocess.Popen[bytes]]) -> None:
    live = [proc for proc in processes if proc.poll() is None]
    for proc in live:
        try:
            if os.name == "nt":
                proc.terminate()
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    for proc in live:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "nt":
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    for proc in live:
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def main() -> int:
    # Import only after the script's project environment is available.
    from forge_conversationalist.startup_preflight import inspect_environment

    env_file = ROOT / ".env.local"
    report = inspect_environment(env_file, check_livekit=True)
    if report["outcome"] != "configuration_ready":
        print("Conversationalist startup preflight failed:", file=sys.stderr)
        for issue in report["issues"]:
            print(f"- {issue}", file=sys.stderr)
        return 2

    worker_values = dotenv_values(env_file)
    effective = {**{k: str(v) for k, v in worker_values.items() if v is not None}, **os.environ}
    puppeteer_configured = bool(str(effective.get("PUPPETEER_ENSEMBLE_CONFIG", "")).strip())
    print(f"Conversationalist: {WEB_URL}", flush=True)
    print(f"Puppeteer ensemble configured: {'yes' if puppeteer_configured else 'no'}", flush=True)
    print("LiveKit is reachable. Press Ctrl-C to stop worker and web.", flush=True)

    commands = [
        ([sys.executable, "-m", "forge_conversationalist.agent", "dev"], ROOT, None),
        (["npm", "run", "dev"], ROOT / "web", _web_environment()),
    ]
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for command, cwd, env in commands:
            kwargs: dict[str, object] = {"cwd": cwd, "env": env}
            if os.name != "nt":
                kwargs["start_new_session"] = True
            processes.append(subprocess.Popen(command, **kwargs))
        while True:
            for proc in processes:
                code = proc.poll()
                if code is not None:
                    _stop(processes)
                    return code if code != 0 else 1
            time.sleep(0.2)
    except KeyboardInterrupt:
        _stop(processes)
        return 0
    except BaseException:
        _stop(processes)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
