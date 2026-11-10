from __future__ import annotations

import importlib.util
import signal
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev.py"
spec = importlib.util.spec_from_file_location("local_dev_launcher", SCRIPT)
launcher = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(launcher)


def test_web_environment_prefers_shell_then_web_then_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    (tmp_path / ".env.local").write_text(
        "LIVEKIT_URL=ws://worker\nLIVEKIT_API_KEY=worker-key\n"
        "LIVEKIT_API_SECRET=worker-secret\nCONVERSATIONALIST_ACCESS_TOKEN=worker-token\n"
        "OPENROUTER_API_KEY=worker-only\n"
    )
    web = tmp_path / "web"
    web.mkdir()
    (web / ".env.local").write_text("LIVEKIT_URL=ws://web\nLIVEKIT_API_KEY=web-key\n")
    monkeypatch.setenv("LIVEKIT_API_KEY", "shell-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "shell-worker-secret")
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    monkeypatch.delenv("CONVERSATIONALIST_ACCESS_TOKEN", raising=False)
    values = launcher._web_environment()
    assert values["LIVEKIT_URL"] == "ws://web"
    assert values["LIVEKIT_API_KEY"] == "shell-key"
    assert values["LIVEKIT_API_SECRET"] == "worker-secret"
    assert values["CONVERSATIONALIST_ACCESS_TOKEN"] == "worker-token"
    assert "OPENROUTER_API_KEY" not in values
    assert "shell-worker-secret" not in values.values()


def test_launcher_failure_stops_sibling_and_returns_failure(monkeypatch, capsys):
    import forge_conversationalist.startup_preflight as preflight

    monkeypatch.setattr(preflight, "inspect_environment", lambda *_a, **_k: {
        "outcome": "configuration_ready", "issues": [],
    })
    monkeypatch.setattr(launcher, "ROOT", Path("/tmp/fake-project"))
    monkeypatch.setattr(launcher, "_web_environment", dict)
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    killed = []

    class Proc:
        def __init__(self, _command, **_kwargs):
            self.pid = 100 + len(created)
            self.calls = len(created)
            created.append(self)
            self.waited = False

        def poll(self):
            return 7 if self.calls == 0 else None

        def wait(self, timeout=None):
            self.waited = True

    created = []
    monkeypatch.setattr(launcher.subprocess, "Popen", Proc)
    monkeypatch.setattr(launcher.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    assert launcher.main() == 7
    assert len(created) == 2
    assert killed == [(101, signal.SIGTERM)]
    assert created[1].waited
    output = capsys.readouterr().out
    assert "http://localhost:3000" in output
    assert "Puppeteer ensemble configured: no" in output


def test_unreachable_livekit_preflight_does_not_start_processes(monkeypatch, capsys):
    import forge_conversationalist.startup_preflight as preflight

    monkeypatch.setattr(preflight, "inspect_environment", lambda *_a, **_k: {
        "outcome": "configuration_incomplete",
        "issues": ["LiveKit endpoint is not reachable; start/check the LiveKit service."],
    })
    monkeypatch.setattr(
        launcher.subprocess, "Popen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not launch")),
    )
    assert launcher.main() == 2
    assert "LiveKit endpoint is not reachable" in capsys.readouterr().err


def test_ctrl_c_stops_both_children(monkeypatch):
    import forge_conversationalist.startup_preflight as preflight

    monkeypatch.setattr(preflight, "inspect_environment", lambda *_a, **_k: {
        "outcome": "configuration_ready", "issues": [],
    })
    monkeypatch.setattr(launcher, "ROOT", Path("/tmp/fake-project"))
    monkeypatch.setattr(launcher, "_web_environment", dict)
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt))
    killed = []

    class Proc:
        pid = 200

        def __init__(self, *_args, **_kwargs):
            self.stopped = False

        def poll(self):
            return None if not self.stopped else -signal.SIGTERM

        def wait(self, timeout=None):
            self.stopped = True

    monkeypatch.setattr(launcher.subprocess, "Popen", Proc)
    monkeypatch.setattr(launcher.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    assert launcher.main() == 0
    assert killed == [(200, signal.SIGTERM), (200, signal.SIGTERM)]
