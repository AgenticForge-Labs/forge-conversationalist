from __future__ import annotations

import json

from forge_conversationalist.startup_preflight import inspect_environment


def _clear_worker_env(monkeypatch):
    for key in (
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
        "CONVERSATIONALIST_ACCESS_TOKEN", "CONVERSATIONALIST_PRESET",
        "CONVERSATIONALIST_STT_PROVIDER", "CONVERSATIONALIST_LLM_PROVIDER",
        "CONVERSATIONALIST_TTS_PROVIDER", "OPENROUTER_API_KEY",
        "PUPPETEER_ENSEMBLE_CONFIG",
    ):
        monkeypatch.delenv(key, raising=False)


def test_preflight_reports_missing_live_config_without_echoing_values(tmp_path, monkeypatch):
    _clear_worker_env(monkeypatch)
    env_file = tmp_path / ".env.local"
    env_file.write_text("LIVEKIT_API_KEY=private-key-value\n")
    report = inspect_environment(env_file)
    rendered = json.dumps(report)
    assert report["outcome"] == "configuration_incomplete"
    assert any("LIVEKIT_URL" in issue for issue in report["issues"])
    assert "private-key-value" not in rendered
    assert report["livekit_session_started"] is False
    assert report["physical_motion_dispatched"] is False


def test_preflight_accepts_static_livekit_setup_and_requires_selected_provider_key(tmp_path, monkeypatch):
    _clear_worker_env(monkeypatch)
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "LIVEKIT_URL=wss://livekit.example\n"
        "LIVEKIT_API_KEY=key\nLIVEKIT_API_SECRET=secret\n"
        "CONVERSATIONALIST_ACCESS_TOKEN=browser-token\n"
        "CONVERSATIONALIST_PRESET=openrouter\n"
    )
    report = inspect_environment(env_file)
    assert report["outcome"] == "configuration_incomplete"
    assert any("OPENROUTER_API_KEY" in issue for issue in report["issues"])
    env_file.write_text(env_file.read_text() + "OPENROUTER_API_KEY=provider-key\n")
    report = inspect_environment(env_file)
    assert report["outcome"] == "configuration_ready"
    assert report["selected_provider_credentials_present"] is True


def test_livekit_probe_reports_unreachable_without_printing_endpoint(tmp_path, monkeypatch):
    _clear_worker_env(monkeypatch)
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "LIVEKIT_URL=ws://private-host.invalid:7880\n"
        "LIVEKIT_API_KEY=key\nLIVEKIT_API_SECRET=secret\n"
        "CONVERSATIONALIST_ACCESS_TOKEN=token\n"
    )

    def refused(*_args, **_kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr("forge_conversationalist.startup_preflight.socket.create_connection", refused)
    report = inspect_environment(env_file, check_livekit=True)
    rendered = json.dumps(report)
    assert report["livekit_endpoint"] == "unreachable"
    assert report["outcome"] == "configuration_incomplete"
    assert "private-host.invalid" not in rendered
    assert "confirm LIVEKIT_URL" in report["issues"][0]


def test_motion_demo_preflight_rejects_hold_and_incomplete_presence(tmp_path, monkeypatch):
    _clear_worker_env(monkeypatch)
    env_file = tmp_path / ".env.local"
    env_file.write_text("LIVEKIT_URL=wss://example.test\nLIVEKIT_API_KEY=k\n"
                        "LIVEKIT_API_SECRET=s\nCONVERSATIONALIST_ACCESS_TOKEN=t\n"
                        "PUPPETEER_ENSEMBLE_CONFIG=room.json\n")
    from types import SimpleNamespace
    selected = {"configuration": "hold_only", "configured_behaviors": []}
    monkeypatch.setattr("forge_conversationalist.startup_preflight.load_integrations",
        lambda _env: SimpleNamespace(ensemble=SimpleNamespace(preflight=lambda: {"soarm_motion": {"arm": selected}})))
    assert inspect_environment(env_file)["outcome"] == "configuration_ready"
    assert inspect_environment(env_file, require_soarm_motion=True)["outcome"] == "configuration_incomplete"
    selected.update(configuration="motion_configured", configured_behaviors=["idle", "speaking"])
    assert inspect_environment(env_file, require_soarm_motion=True)["outcome"] == "configuration_incomplete"
    selected["configured_behaviors"].append("listening")
    assert inspect_environment(env_file, require_soarm_motion=True)["outcome"] == "configuration_ready"

