"""Write schema-v4 Puppeteer ensemble files for tests. Nothing here opens a device."""

from __future__ import annotations

import json
from pathlib import Path

IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
ALL_KINDS = ("reachy", "stackchan", "soarm101")


def provider(kind: str, ident: str | None = None, **extra) -> dict:
    ident = ident or kind
    if kind == "reachy":
        body = {"backend": "reachy_sdk", "device": "reachy", "instance_id": ident,
                "allowed_tools": ["head_pose", "pose_and_antennas", "play_move", "device_stop",
                                  "wake_up", "original_idle"]}
    elif kind == "stackchan":
        body = {"backend": "stackchan_sdk", "device": "stackchan", "instance_id": ident,
                "allowed_tools": ["head_move", "face_update", "device_stop"]}
    else:
        body = {"instance_id": ident, "mode": "existing_broker",
                "broker_url": "http://127.0.0.1:8765", "token_env": "SOARM_TOKEN",
                "allowed_tools": ["robot_capabilities", "robot_state", "stop"]}
    body.update(extra)
    return body


def write_ensemble(
    tmp_path: Path, kinds=ALL_KINDS, *, physical: bool = True, options: dict | None = None,
    providers: dict | None = None, ensemble: dict | None = None, name: str = "ensemble.json",
) -> Path:
    """``options``/``providers`` map kind -> dict (providers are merged over the defaults);
    ``ensemble`` is merged over the top-level ensemble file."""
    refs = []
    for kind in kinds:
        body = {"kind": kind, "id": kind, "provider": provider(kind, **(providers or {}).get(kind, {}))}
        if (options or {}).get(kind) is not None:
            body["options"] = options[kind]
        (tmp_path / f"{kind}.json").write_text(json.dumps(body), encoding="utf-8")
        refs.append({"kind": kind, "id": kind, "file": f"{kind}.json"})
    data = {"schema_version": 4, "physical": physical, "robots": refs,
            "ownership_db": str(tmp_path / "owner.db"), "action_journal": str(tmp_path / "action.db")}
    if "reachy" in kinds:
        neutral = tmp_path / "head.json"
        neutral.write_text(json.dumps(IDENTITY), encoding="utf-8")
        data["reachy_neutral_head"] = str(neutral)
    data.update(ensemble or {})
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path
