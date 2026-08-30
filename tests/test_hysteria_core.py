"""Supervising the hysteria2 daemon.

No daemon is run here — the tests stand in for the process, and check the two
things that matter around it: that the panel starts it against a configuration
it just rendered, and that a hysteria which will not start stays a warning
rather than taking anything else down with it.
"""

import subprocess

import pytest

from app.hysteria import config as hysteria_config
from app.hysteria.config import HysteriaConfigError
from app.hysteria.core import HysteriaCore


class FakeProcess:
    """A process that is running until it is told otherwise."""

    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = False
        self.stderr = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15


@pytest.fixture
def core(monkeypatch, tmp_path):
    core = HysteriaCore("/nonexistent/hysteria")
    # The version is looked up by running the binary, which is not there.
    monkeypatch.setattr(core, "_version", "2.6.0")
    monkeypatch.setattr(hysteria_config, "write", lambda: str(tmp_path / "hysteria.yaml"))
    return core


@pytest.fixture
def spawned(monkeypatch):
    """Captures the command the core would have run."""
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(HysteriaCore, "_HysteriaCore__capture_process_logs", lambda self: None)
    return calls


class TestVersion:
    def test_a_missing_binary_is_not_an_error(self):
        assert HysteriaCore("/nonexistent/hysteria").get_version() is None

    def test_the_version_is_read_out_of_the_report(self, monkeypatch):
        output = b"Client/Server: server\nVersion: v2.6.0\nBuildDate: 2026-01-01\n"
        monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: output)

        assert HysteriaCore().get_version() == "2.6.0"


class TestStarting:
    def test_it_runs_the_configuration_it_just_rendered(self, core, spawned, tmp_path):
        core.start()

        assert spawned == [["/nonexistent/hysteria", "server", "--config", str(tmp_path / "hysteria.yaml")]]
        assert core.config_path == str(tmp_path / "hysteria.yaml")

    def test_starting_twice_is_refused(self, core, spawned):
        core.start()

        with pytest.raises(RuntimeError, match="already"):
            core.start()

    def test_a_configuration_that_cannot_be_rendered_stops_the_start(self, core, spawned, monkeypatch):
        def no_certificate():
            raise HysteriaConfigError("Hysteria2 needs a TLS certificate.")

        monkeypatch.setattr(hysteria_config, "write", no_certificate)

        with pytest.raises(HysteriaConfigError):
            core.start()

        assert spawned == []
        assert core.started is False


class TestStopping:
    def test_stopping_what_never_started_does_nothing(self, core):
        core.stop()

        assert core.started is False

    def test_a_running_daemon_is_terminated(self, core, spawned):
        core.start()
        process = core.process

        core.stop()

        assert process.terminated is True
        assert core.started is False

    def test_a_daemon_that_exited_reads_as_stopped(self, core, spawned):
        core.start()
        core.process.returncode = 1

        assert core.started is False

    def test_restart_brings_it_back(self, core, spawned):
        core.start()
        first = core.process

        core.restart()

        assert first.terminated is True
        assert core.started is True
        assert len(spawned) == 2


class TestHealthCheck:
    """What keeps a crashed daemon from staying down, called on a timer."""

    @staticmethod
    def run(monkeypatch, *, enabled, core):
        from app import hysteria

        monkeypatch.setattr(hysteria, "HYSTERIA_ENABLED", enabled)
        monkeypatch.setattr(hysteria, "core", core)
        hysteria.ensure_running()

    def test_a_stopped_daemon_is_started_again(self, monkeypatch, core, spawned):
        self.run(monkeypatch, enabled=True, core=core)

        assert core.started is True

    def test_nothing_runs_while_the_feature_is_off(self, monkeypatch, core, spawned):
        self.run(monkeypatch, enabled=False, core=core)

        assert core.started is False

    def test_a_running_daemon_is_left_alone(self, monkeypatch, core, spawned):
        core.start()

        self.run(monkeypatch, enabled=True, core=core)

        assert len(spawned) == 1

    def test_a_failure_to_start_is_reported_not_raised(self, monkeypatch, core, spawned):
        def no_certificate():
            raise HysteriaConfigError("no certificate")

        monkeypatch.setattr(hysteria_config, "write", no_certificate)

        self.run(monkeypatch, enabled=True, core=core)

        assert core.started is False
