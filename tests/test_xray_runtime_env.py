"""The environment the panel starts the core in.

The core is a Go program the panel does not build, so this is the only handle
it has on what the core's runtime does while it runs — and the only place to
be careful about what of the panel's own environment reaches it.
"""

from importlib import import_module

import pytest

# `app.xray.core` is the running core instance, not this module -- the package
# binds one over the submodule's name. Reach the module itself by name.
xray_core = import_module("app.xray.core")


@pytest.fixture
def memory(monkeypatch):
    """A machine with 8 GB, whatever the one running the tests has."""
    monkeypatch.setattr(xray_core, "XRAY_MEMORY_LIMIT_PERCENT", 50)

    class Memory:
        total = 8 * 1024 ** 3

    import psutil

    monkeypatch.setattr(psutil, "virtual_memory", lambda: Memory)


class TestRuntimeEnv:
    def test_the_assets_path_is_passed_on(self):
        env = xray_core.runtime_env("/usr/local/share/xray")

        assert env["XRAY_LOCATION_ASSET"] == "/usr/local/share/xray"

    def test_the_heap_is_allowed_to_grow_between_collections(self, monkeypatch):
        monkeypatch.setattr(xray_core, "XRAY_GOGC", 200)

        assert xray_core.runtime_env("/assets")["GOGC"] == "200"

    def test_a_larger_heap_is_capped_by_what_the_machine_has(self, memory):
        env = xray_core.runtime_env("/assets")

        assert env["GOMEMLIMIT"] == f"{4 * 1024 ** 3}B"

    def test_the_runtime_is_left_alone_when_both_are_off(self, monkeypatch):
        monkeypatch.setattr(xray_core, "XRAY_GOGC", 0)
        monkeypatch.setattr(xray_core, "XRAY_MEMORY_LIMIT_PERCENT", 0)

        assert xray_core.runtime_env("/assets") == {"XRAY_LOCATION_ASSET": "/assets"}

    def test_a_machine_that_will_not_say_its_memory_gets_no_limit(self, monkeypatch):
        monkeypatch.setattr(xray_core, "XRAY_MEMORY_LIMIT_PERCENT", 60)

        import psutil

        def unavailable():
            raise RuntimeError("no such thing here")

        monkeypatch.setattr(psutil, "virtual_memory", unavailable)

        # A missing ceiling is a worse core, not a core that will not start.
        assert "GOMEMLIMIT" not in xray_core.runtime_env("/assets")

    def test_the_panel_s_own_environment_is_not_handed_over(self, monkeypatch):
        # http_proxy is the one that would quietly send the core's traffic
        # somewhere else; none of the rest is meant for it either.
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:9999")

        assert "http_proxy" not in xray_core.runtime_env("/assets")

    def test_a_core_is_built_with_the_same_environment(self, monkeypatch):
        monkeypatch.setattr(xray_core, "XRAY_GOGC", 150)

        core = xray_core.XRayCore(assets_path="/somewhere/else")

        assert core._env == xray_core.runtime_env("/somewhere/else")
        assert core._env["GOGC"] == "150"
