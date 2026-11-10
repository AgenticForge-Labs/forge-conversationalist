"""Static, secret-safe checks for a local Conversationalist worker setup."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

from .config import load_profile
from .integrations import load_integrations

REQUIRED = (
    "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
    "CONVERSATIONALIST_ACCESS_TOKEN",
)


def inspect_environment(env_file: Path, *, check_livekit: bool = False,
                        require_soarm_motion: bool = False) -> dict[str, object]:
    file_values = dotenv_values(env_file) if env_file.is_file() else {}
    env = {**{k: v for k, v in file_values.items() if v is not None}, **os.environ}
    present = {name: bool(str(env.get(name, "")).strip()) for name in REQUIRED}
    issues: list[str] = [f"Set {name} in {env_file} or the worker environment." for name, ok in present.items() if not ok]

    url = str(env.get("LIVEKIT_URL", "")).strip()
    parsed = urlparse(url)
    livekit_endpoint = "not_checked"
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = -1
    if present["LIVEKIT_URL"] and (
        parsed.scheme not in {"ws", "wss"} or not parsed.hostname or parsed_port == -1
    ):
        issues.append("LIVEKIT_URL must be a ws:// or wss:// LiveKit server URL.")
    elif check_livekit and present["LIVEKIT_URL"]:
        port = parsed_port or (443 if parsed.scheme == "wss" else 80)
        try:
            with socket.create_connection((parsed.hostname, port), timeout=3):
                livekit_endpoint = "tcp_reachable"
        except OSError:
            livekit_endpoint = "unreachable"
            issues.append(
                "LiveKit endpoint is not reachable; start/check the LiveKit service and confirm LIVEKIT_URL."
            )

    profile_name = None
    try:
        profile = load_profile(env)
        profile_name = str(env.get("CONVERSATIONALIST_PRESET", "livekit"))
        if profile.requires_openrouter and not str(env.get("OPENROUTER_API_KEY", "")).strip():
            issues.append("The selected speech profile uses OpenRouter; set OPENROUTER_API_KEY in the worker environment.")
    except (TypeError, ValueError) as exc:
        issues.append(f"Speech profile is invalid: {exc}")

    ensemble = None
    if str(env.get("PUPPETEER_ENSEMBLE_CONFIG", "")).strip():
        try:
            configured = load_integrations(env).ensemble
            if configured is None:
                issues.append("PUPPETEER_ENSEMBLE_CONFIG was set but no native ensemble was loaded.")
            else:
                ensemble = configured.preflight()
                if require_soarm_motion:
                    arms = ensemble.get("soarm_motion", {})
                    if not arms or any(
                        item.get("configuration") != "motion_configured"
                        or not {"idle", "listening", "speaking"} <= set(item.get("configured_behaviors", []))
                        for item in arms.values()
                    ):
                        issues.append("SO-ARM motion demo requires configured idle, listening and speaking choreography; hold-only is not a motion demo.")
        except (OSError, TypeError, ValueError, PermissionError) as exc:
            issues.append(
                "Puppeteer static preflight failed "
                f"({type(exc).__name__}); check the config path, schema, exact performer IDs, "
                "reviewed neutral file, grants, and REVIEW_REQUIRED placeholders."
            )

    if require_soarm_motion and ensemble is None:
        issues.append("SO-ARM motion demo requires a valid configured ensemble.")

    return {
        "schema": "forge.conversationalist_preflight.v1",
        "outcome": "configuration_ready" if not issues else "configuration_incomplete",
        "env_file_found": env_file.is_file(),
        "required_values_present": present,
        "speech_preset": profile_name,
        "livekit_endpoint": livekit_endpoint,
        "selected_provider_credentials_present": (
            not profile.requires_openrouter or bool(str(env.get("OPENROUTER_API_KEY", "")).strip())
        ) if profile_name is not None else False,
        "puppeteer": ensemble,
        "livekit_tcp_probe_attempted": check_livekit,
        "livekit_session_started": False,
        "voice_session_started": False,
        "physical_motion_dispatched": False,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check local worker configuration without starting voice or connecting robots.",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    parser.add_argument(
        "--check-livekit", action="store_true",
        help="perform a read-only TCP reachability probe (does not authenticate)",
    )
    parser.add_argument("--require-soarm-motion", action="store_true",
                        help="fail if the selected SO-ARM room is hold-only or lacks presence choreography")
    args = parser.parse_args(argv)
    report = inspect_environment(args.env_file, check_livekit=args.check_livekit,
                                 require_soarm_motion=args.require_soarm_motion)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["outcome"] == "configuration_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
