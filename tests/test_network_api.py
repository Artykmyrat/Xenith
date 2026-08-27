"""The /api/network endpoints: kernel tunables and the profiles that group them.

The whole screen is sudo-only and refuses to write anything until the panel is
configured for it, so those two gates get the most attention here.
"""

import pytest

from app.db import crud
from app.utils import sysctl
from app.utils.sysctl_catalog import BASELINE, TUNABLES, section_titles

from conftest import auth


def settings_of(body) -> dict:
    return {
        setting["key"]: setting
        for section in body["sections"]
        for setting in section["settings"]
    }


class TestAuthorisation:
    ENDPOINTS = [
        ("get", "/api/network"),
        ("put", "/api/network"),
        ("post", "/api/network/reset"),
        ("get", "/api/network/profiles"),
        ("post", "/api/network/profiles"),
        ("put", "/api/network/profiles/1"),
        ("delete", "/api/network/profiles/1"),
        ("post", "/api/network/profiles/1/apply"),
    ]

    @staticmethod
    def call(client, method, path, **kwargs):
        # GET and DELETE carry no body; the rest need one to get past validation.
        if method not in ("get", "delete"):
            kwargs["json"] = {}
        return getattr(client, method)(path, **kwargs)

    @pytest.mark.parametrize("method, path", ENDPOINTS)
    def test_no_credentials_is_rejected(self, client, method, path):
        assert self.call(client, method, path).status_code == 401

    @pytest.mark.parametrize("method, path", ENDPOINTS)
    def test_a_reseller_is_rejected(self, client, plain_admin, method, path):
        response = self.call(client, method, path, headers=auth(plain_admin))

        assert response.status_code == 403


class TestReadSettings:
    def test_every_managed_key_is_reported(self, client, sudo_admin, proc):
        body = client.get("/api/network", headers=auth(sudo_admin)).json()

        assert set(settings_of(body)) == {tunable.key for tunable in TUNABLES}

    def test_settings_arrive_grouped_into_sections(self, client, sudo_admin, proc):
        body = client.get("/api/network", headers=auth(sudo_admin)).json()

        assert [section["id"] for section in body["sections"]] == [s for s, _ in section_titles()]
        assert all(section["settings"] for section in body["sections"])

    def test_each_setting_carries_what_the_dashboard_needs(self, client, sudo_admin, proc):
        setting = settings_of(client.get("/api/network", headers=auth(sudo_admin)).json())["vm.swappiness"]

        assert setting["kind"] == "int"
        assert setting["baseline"] == "10"
        assert setting["description"]

    def test_the_live_value_is_what_is_shown(self, client, sudo_admin, proc):
        (proc / "vm/swappiness").write_text("60\n")

        setting = settings_of(client.get("/api/network", headers=auth(sudo_admin)).json())["vm.swappiness"]

        assert setting["value"] == "60"

    def test_a_value_away_from_the_baseline_is_flagged(self, client, sudo_admin, proc):
        (proc / "vm/swappiness").write_text("60\n")

        settings = settings_of(client.get("/api/network", headers=auth(sudo_admin)).json())

        assert settings["vm.swappiness"]["customised"] is True
        assert settings["net.ipv4.ip_forward"]["customised"] is False

    def test_reading_works_even_while_writing_is_off(self, client, sudo_admin, proc):
        body = client.get("/api/network", headers=auth(sudo_admin)).json()

        assert body["enabled"] is False
        assert body["writable"] is False
        assert "SYSCTL_ENABLED" in body["reason"]

    def test_a_configured_host_reports_itself_writable(self, client, sudo_admin, tunable_host):
        body = client.get("/api/network", headers=auth(sudo_admin)).json()

        assert body["writable"] is True
        assert body["reason"] is None

    def test_the_managed_file_is_named(self, client, sudo_admin, conf):
        body = client.get("/api/network", headers=auth(sudo_admin)).json()

        assert body["managed_file"] == str(conf)

    def test_interfaces_are_listed_for_context(self, client, sudo_admin, proc):
        body = client.get("/api/network", headers=auth(sudo_admin)).json()

        assert isinstance(body["interfaces"], list)
        assert all("name" in interface for interface in body["interfaces"])


class TestWriteSettings:
    def test_a_value_is_written_and_applied(self, client, sudo_admin, tunable_host, conf):
        response = client.put(
            "/api/network", json={"settings": {"vm.swappiness": "1"}}, headers=auth(sudo_admin)
        )

        assert response.status_code == 200
        assert response.json()["applied"] == ["vm.swappiness"]
        assert "vm.swappiness = 1" in conf.read_text()

    def test_the_fresh_state_comes_back_with_the_result(self, client, sudo_admin, tunable_host, proc):
        body = client.put(
            "/api/network", json={"settings": {"vm.swappiness": "1"}}, headers=auth(sudo_admin)
        ).json()

        assert settings_of(body["settings"])["vm.swappiness"]["baseline"] == "10"

    def test_a_key_outside_the_catalogue_is_a_400(self, client, sudo_admin, tunable_host):
        response = client.put(
            "/api/network",
            json={"settings": {"kernel.core_pattern": "|/bin/sh"}},
            headers=auth(sudo_admin),
        )

        assert response.status_code == 400
        assert "not a setting this panel manages" in response.json()["detail"]

    def test_a_value_carrying_a_second_directive_is_a_400(self, client, sudo_admin, tunable_host, conf):
        response = client.put(
            "/api/network",
            json={"settings": {"vm.swappiness": "10\nkernel.sysrq = 1"}},
            headers=auth(sudo_admin),
        )

        assert response.status_code == 400
        assert not conf.exists()

    def test_an_empty_request_is_a_validation_error(self, client, sudo_admin, tunable_host):
        response = client.put("/api/network", json={"settings": {}}, headers=auth(sudo_admin))

        assert response.status_code == 422

    def test_writing_while_disabled_is_a_400(self, client, sudo_admin, proc, conf):
        response = client.put(
            "/api/network", json={"settings": {"vm.swappiness": "1"}}, headers=auth(sudo_admin)
        )

        assert response.status_code == 400
        assert "SYSCTL_ENABLED" in response.json()["detail"]
        assert not conf.exists()

    def test_a_key_the_kernel_refuses_is_reported_not_hidden(
        self, client, sudo_admin, enabled, proc, conf, monkeypatch
    ):
        import subprocess

        def refuse(args, **kwargs):
            return subprocess.CompletedProcess(
                args, 255, stdout="",
                stderr='sysctl: setting key "kernel.sysrq": Read-only file system\n',
            )

        monkeypatch.setattr(sysctl.subprocess, "run", refuse)

        body = client.put(
            "/api/network",
            json={"settings": {"vm.swappiness": "1", "kernel.sysrq": "0"}},
            headers=auth(sudo_admin),
        ).json()

        assert body["applied"] == ["vm.swappiness"]
        assert body["failed"] == [{"key": "kernel.sysrq", "message": "Read-only file system"}]

    def test_reset_writes_the_whole_baseline(self, client, sudo_admin, tunable_host, conf):
        client.put("/api/network", json={"settings": {"vm.swappiness": "1"}}, headers=auth(sudo_admin))

        body = client.post("/api/network/reset", headers=auth(sudo_admin)).json()

        assert set(body["applied"]) == set(BASELINE)
        assert "vm.swappiness = 10" in conf.read_text()


class TestProfiles:
    def test_the_builtin_profile_exists_on_first_read(self, client, sudo_admin, proc):
        profiles = client.get("/api/network/profiles", headers=auth(sudo_admin)).json()

        assert len(profiles) == 1
        assert profiles[0]["builtin"] is True
        assert profiles[0]["settings"] == BASELINE

    def test_reading_twice_does_not_create_a_second_one(self, client, sudo_admin, proc):
        client.get("/api/network/profiles", headers=auth(sudo_admin))
        profiles = client.get("/api/network/profiles", headers=auth(sudo_admin)).json()

        assert len(profiles) == 1

    def test_the_builtin_profile_follows_the_catalogue(self, client, db, sudo_admin, proc):
        crud.get_network_profiles(db)
        stored = db.query(crud.NetworkProfile).first()
        stored.settings = {"vm.swappiness": "99"}
        db.commit()

        profiles = client.get("/api/network/profiles", headers=auth(sudo_admin)).json()

        assert profiles[0]["settings"] == BASELINE

    def test_a_profile_can_be_saved_from_explicit_settings(self, client, sudo_admin, proc):
        response = client.post(
            "/api/network/profiles",
            json={"name": "Low latency", "description": "for gaming", "settings": {"vm.swappiness": "1"}},
            headers=auth(sudo_admin),
        )

        assert response.status_code == 200
        assert response.json()["settings"] == {"vm.swappiness": "1"}
        assert response.json()["builtin"] is False

    def test_saving_without_settings_captures_what_is_running(self, client, sudo_admin, proc):
        (proc / "vm/swappiness").write_text("60\n")

        body = client.post(
            "/api/network/profiles", json={"name": "As found"}, headers=auth(sudo_admin)
        ).json()

        assert body["settings"]["vm.swappiness"] == "60"
        assert set(body["settings"]) == set(BASELINE)

    def test_a_profile_with_an_unknown_key_is_a_400(self, client, sudo_admin, proc):
        response = client.post(
            "/api/network/profiles",
            json={"name": "Bad", "settings": {"kernel.core_pattern": "|/bin/sh"}},
            headers=auth(sudo_admin),
        )

        assert response.status_code == 400

    def test_a_duplicate_name_is_a_conflict(self, client, sudo_admin, proc):
        body = {"name": "Tuned", "settings": {"vm.swappiness": "1"}}
        client.post("/api/network/profiles", json=body, headers=auth(sudo_admin))

        assert client.post("/api/network/profiles", json=body, headers=auth(sudo_admin)).status_code == 409

    def test_a_blank_name_is_a_validation_error(self, client, sudo_admin, proc):
        response = client.post("/api/network/profiles", json={"name": "   "}, headers=auth(sudo_admin))

        assert response.status_code == 422

    def test_profiles_are_listed_with_the_builtin_first(self, client, sudo_admin, proc):
        client.post(
            "/api/network/profiles",
            json={"name": "Aaa first alphabetically", "settings": {"vm.swappiness": "1"}},
            headers=auth(sudo_admin),
        )

        profiles = client.get("/api/network/profiles", headers=auth(sudo_admin)).json()

        assert [profile["builtin"] for profile in profiles] == [True, False]

    def test_a_profile_can_be_renamed_and_retuned(self, client, sudo_admin, proc):
        created = client.post(
            "/api/network/profiles",
            json={"name": "Tuned", "settings": {"vm.swappiness": "1"}},
            headers=auth(sudo_admin),
        ).json()

        response = client.put(
            f"/api/network/profiles/{created['id']}",
            json={"name": "Retuned", "settings": {"vm.swappiness": "5"}},
            headers=auth(sudo_admin),
        )

        assert response.json()["name"] == "Retuned"
        assert response.json()["settings"] == {"vm.swappiness": "5"}
        assert response.json()["updated_at"]

    def test_the_builtin_profile_cannot_be_edited(self, client, sudo_admin, proc):
        builtin = client.get("/api/network/profiles", headers=auth(sudo_admin)).json()[0]

        response = client.put(
            f"/api/network/profiles/{builtin['id']}", json={"name": "Mine"}, headers=auth(sudo_admin)
        )

        assert response.status_code == 403
        assert "Save a copy" in response.json()["detail"]

    def test_the_builtin_profile_cannot_be_deleted(self, client, sudo_admin, proc):
        builtin = client.get("/api/network/profiles", headers=auth(sudo_admin)).json()[0]

        response = client.delete(f"/api/network/profiles/{builtin['id']}", headers=auth(sudo_admin))

        assert response.status_code == 403

    def test_a_saved_profile_can_be_deleted(self, client, db, sudo_admin, proc):
        created = client.post(
            "/api/network/profiles",
            json={"name": "Tuned", "settings": {"vm.swappiness": "1"}},
            headers=auth(sudo_admin),
        ).json()

        assert client.delete(f"/api/network/profiles/{created['id']}", headers=auth(sudo_admin)).status_code == 200
        assert crud.get_network_profile(db, created["id"]) is None

    def test_an_unknown_profile_is_a_404(self, client, sudo_admin, proc):
        assert client.delete("/api/network/profiles/999", headers=auth(sudo_admin)).status_code == 404
        assert client.put("/api/network/profiles/999", json={}, headers=auth(sudo_admin)).status_code == 404
        assert client.post("/api/network/profiles/999/apply", headers=auth(sudo_admin)).status_code == 404


class TestApplyProfile:
    def test_applying_writes_everything_the_profile_holds(self, client, sudo_admin, tunable_host, conf):
        created = client.post(
            "/api/network/profiles",
            json={"name": "Tuned", "settings": {"vm.swappiness": "1", "net.ipv4.ip_forward": "0"}},
            headers=auth(sudo_admin),
        ).json()

        body = client.post(
            f"/api/network/profiles/{created['id']}/apply", headers=auth(sudo_admin)
        ).json()

        assert body["applied"] == ["net.ipv4.ip_forward", "vm.swappiness"]
        assert "vm.swappiness = 1" in conf.read_text()

    def test_the_builtin_profile_can_be_applied(self, client, sudo_admin, tunable_host, conf):
        builtin = client.get("/api/network/profiles", headers=auth(sudo_admin)).json()[0]

        body = client.post(
            f"/api/network/profiles/{builtin['id']}/apply", headers=auth(sudo_admin)
        ).json()

        assert set(body["applied"]) == set(BASELINE)

    def test_applying_while_disabled_is_a_400(self, client, sudo_admin, proc, conf):
        builtin = client.get("/api/network/profiles", headers=auth(sudo_admin)).json()[0]

        response = client.post(
            f"/api/network/profiles/{builtin['id']}/apply", headers=auth(sudo_admin)
        )

        assert response.status_code == 400
        assert not conf.exists()
