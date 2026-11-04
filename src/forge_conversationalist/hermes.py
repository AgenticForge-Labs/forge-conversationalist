"""Minimal asynchronous Hermes Runs API client for API-owned delegated tasks.

Important: /v1/runs cannot take ownership of another surface's CLI/TUI/ACP
session. A voice room creates its own distinct session. No auto-retry
on unknown admission outcome; run IDs are scoped to this voice room.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import httpx

from .integrations import HermesConfig

_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
_MAX_INPUT = 4000
_MAX_OUTPUT = 1600


class HermesUnavailable(Exception):
    """Expected upstream error; never contains private server response text."""


@dataclass
class HermesRunsClient:
    config: HermesConfig
    session_id: str = field(default_factory=lambda: f"forge-voice-{uuid4().hex}")
    _runs: set[str] = field(default_factory=set, init=False, repr=False)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.key}"}

    async def submit(self, instruction: str) -> dict[str, str]:
        """Admit a task once and return its accepted run ID.

        An ambiguous transport failure is not automatically retried under
        a new idempotency key: Hermes might already have started the task.
        """
        instruction = instruction.strip()
        if not instruction or len(instruction) > _MAX_INPUT:
            raise ValueError("Hermes task must contain 1-4000 characters")
        headers = self._headers()
        headers["Idempotency-Key"] = uuid4().hex
        payload = {"input": instruction, "session_id": self.session_id}
        try:
            async with httpx.AsyncClient(base_url=self.config.url, timeout=15.0) as http:
                response = await http.post("/v1/runs", headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise HermesUnavailable("Hermes run submission failed; execution outcome may be unknown") from exc
        if not isinstance(body, dict) or not isinstance(body.get("run_id"), str):
            raise HermesUnavailable("Hermes returned an invalid run admission")
        run_id = body["run_id"]
        if not run_id or len(run_id) > 128 or not all(ch.isalnum() or ch in "-_" for ch in run_id):
            raise HermesUnavailable("Hermes returned an invalid run ID")
        self._runs.add(run_id)
        return {"run_id": run_id, "status": str(body.get("status", "started"))}


    async def status(self, run_id: str) -> dict[str, str]:
        """Read only runs this voice session itself submitted."""
        if run_id not in self._runs:
            raise ValueError("Unknown run for this conversation")
        try:
            async with httpx.AsyncClient(base_url=self.config.url, timeout=10.0) as http:
                response = await http.get(f"/v1/runs/{run_id}", headers=self._headers())
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise HermesUnavailable("Hermes run status is unavailable") from exc
        if not isinstance(body, dict) or body.get("run_id") != run_id:
            raise HermesUnavailable("Hermes returned an unexpected run status")
        state = body.get("status")
        if not isinstance(state, str) or not state:
            raise HermesUnavailable("Hermes returned an invalid run status")
        output = body.get("output", "")
        result = {"run_id": run_id, "status": state}
        if state == "completed" and isinstance(output, str):
            result["output"] = output[:_MAX_OUTPUT]
            if len(output) > _MAX_OUTPUT:
                result["truncated"] = "true"
        if state in {"failed", "cancelled", "interrupted", "unknown", "waiting_for_approval"}:
            result["note"] = "Hermes requires human review or a separate secure control surface."
        return result
