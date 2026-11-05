"""Read-only operator validation of a Puppeteer ensemble configuration.

No LiveKit, MCP or robot SDK is imported, and no child process is spawned.
Only the on-disk operator JSON is parsed and fingerprinted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ensemble import PuppeteerEnsembleConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate native ensemble operator config without connecting hardware.",
    )
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = PuppeteerEnsembleConfig(args.config).preflight()
    except (OSError, TypeError, ValueError, PermissionError) as exc:
        print(json.dumps({
            "schema": "forge.ensemble_preflight.v1",
            "outcome": "invalid_configuration",
            "reason": type(exc).__name__,
            "sdk_connections_opened": False,
            "physical_motion_dispatched": False,
        }), file=sys.stdout)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
