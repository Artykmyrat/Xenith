"""Configuring the hysteria2 daemon from the panel.

These settings used to live in .env, which meant a rebuilt container for every
port change. They are a row now, so the tests care about the two things that
change: what the panel refuses to store, and what happens to the running daemon
when something is stored.
"""

import pytest

from app import hysteria
from app.db.models import HysteriaSettings
from app.hysteria import settings as hysteria_settings_module
from app.utils import certbot
from conftest import auth
from test_hysteria_config import certificate

PATH = "/api/hysteria/settings"


@pytest.fixture(autouse=True)
def clean_cache():
    """The settings are remembered between calls; each test starts fresh."""
    hysteria_settings_module.invalidate()
    yield
    hysteria_settings_module.invalidate()


@pytest.fixture
def certificates(monkeypatch):
    monkeypatch.setattr(certbot, "CERTBOT_ENABLED", True)
    monkeypatch.setattr(certbot, "list_certificates", lambda: [certificate()])


@pytest.fixture
def idle_core(monkeypatch):
    """A daemon that records what it was asked to do and never runs anything."""
    actions = []

    monkeypatch.setattr(hysteria.core, "start", lambda: actions.append("start"))
    monkeypatch.setattr(hysteria.core, "stop", lambda: actions.append("stop"))
    monkeypatch.setattr(hysteria.core, "restart", lambda: actions.append("restart"))
    monkeypatch.setattr(type(hysteria.core), "started", property(lambda self: False))

    return actions


class TestValidation:
    """What the panel will not store, and why each one would cost something."""

    def test_a_port_outside_the_range_is_refused(self, db):
        with pytest.raises(hysteria_settings_module.HysteriaSettingsError, match="valid port"):
            hysteria_settings_module.save(db, port=70000)

    def test_the_traffic_api_cannot_share_the_daemons_port(self, db):
        with pytest.raises(hysteria_settings_module.HysteriaSettingsError, match="share"):
            hysteria_settings_module.save(db, port=8443, stats_port=8443)

    def test_the_panels_own_port_is_refused(self, db, monkeypatch):
        """Taking it would stop the panel answering, including the auth callback."""
        monkeypatch.setattr(hysteria_settings_module, "UVICORN_PORT", 8000)

        with pytest.raises(hysteria_settings_module.HysteriaSettingsError, match="panel's own"):
            hysteria_settings_module.save(db, port=8000)

    def test_a_domain_that_is_not_one_is_refused(self, db):
        with pytest.raises(hysteria_settings_module.HysteriaSettingsError, match="valid domain"):
            hysteria_settings_module.save(db, domain="not a domain")

    def test_a_masquerade_url_without_a_scheme_is_refused(self, db):
        with pytest.raises(hysteria_settings_module.HysteriaSettingsError, match="http"):
            hysteria_settings_module.save(db, masquerade_url="www.example.com")

    def test_an_empty_masquerade_url_is_allowed(self, db):
        """Not every deployment wants the port to answer like a website."""
        assert hysteria_settings_module.save(db, masquerade_url=None).masquerade_url is None

    @pytest.mark.parametrize("key", ["auth", "tls", "listen", "trafficStats"])
    def test_extra_cannot_take_over_a_key_the_panel_owns(self, db, key):
        """`auth` is the one that matters: overriding it would unhook every user
        from the traffic they generate, and nothing else would look wrong."""
        with pytest.raises(hysteria_settings_module.HysteriaSettingsError, match=key):
            hysteria_settings_module.save(db, extra={key: {"anything": True}})

    def test_extra_may_carry_anything_else(self, db):
        stored = hysteria_settings_module.save(db, extra={"udpIdleTimeout": "90s"})

        assert stored.extra == {"udpIdleTimeout": "90s"}

    def test_extra_has_to_be_a_mapping(self, db):
        with pytest.raises(hysteria_settings_module.HysteriaSettingsError, match="mapping"):
            hysteria_settings_module.save(db, extra=["not", "a", "mapping"])


class TestStoring:
    def test_a_saved_value_is_what_is_read_back(self, db):
        hysteria_settings_module.save(db, port=8443)

        assert hysteria_settings_module.current().port == 8443

    def test_only_the_fields_given_are_touched(self, db):
        hysteria_settings_module.save(db, port=8443, domain="vpn.example.com")
        hysteria_settings_module.save(db, port=9443)

        assert hysteria_settings_module.current().domain == "vpn.example.com"

    def test_a_row_is_created_when_the_seed_never_ran(self, db):
        """A database built from the models rather than through the migration."""
        db.query(HysteriaSettings).delete()
        db.commit()
        hysteria_settings_module.invalidate()

        hysteria_settings_module.save(db, port=8443)

        assert db.query(HysteriaSettings).count() == 1

    def test_the_cache_is_dropped_on_a_change(self, db):
        hysteria_settings_module.current()  # warm it

        hysteria_settings_module.save(db, enabled=True)

        assert hysteria_settings_module.current().enabled is True


class TestTheConfiguration:
    def test_the_rendered_file_follows_the_settings(self, db, certificates):
        hysteria_settings_module.save(db, port=8443, stats_port=25999)

        config = hysteria.config.render()

        assert config["listen"] == ":8443"
        assert config["trafficStats"]["listen"] == "127.0.0.1:25999"

    def test_extra_reaches_the_rendered_file(self, db, certificates):
        hysteria_settings_module.save(db, extra={"udpIdleTimeout": "90s"})

        assert hysteria.config.render()["udpIdleTimeout"] == "90s"

    def test_the_preview_withholds_the_stats_secret(self, db, certificates):
        """It is this process's key to its own daemon and changes every start."""
        preview = hysteria.config.preview()

        assert hysteria.config.STATS_SECRET not in preview
        assert "generated on each start" in preview


class TestTheAPI:
    def test_a_plain_admin_is_not_allowed(self, client, plain_admin):
        assert client.get(PATH, headers=auth(plain_admin)).status_code == 403

    def test_no_credentials_is_rejected(self, client):
        assert client.get(PATH).status_code == 401

    def test_the_settings_come_back_with_the_state(self, client, sudo_admin, certificates):
        body = client.get(PATH, headers=auth(sudo_admin)).json()

        assert body["port"] == 443
        assert body["running"] is False
        assert body["config"].startswith("listen:")
        assert body["certificates"] == ["panel.example.com"]
        assert "auth" in body["reserved_keys"]

    def test_a_configuration_that_will_not_render_says_why(self, client, sudo_admin, monkeypatch):
        monkeypatch.setattr(certbot, "CERTBOT_ENABLED", False)

        body = client.get(PATH, headers=auth(sudo_admin)).json()

        assert body["config"] is None
        assert "CERTBOT_ENABLED" in body["reason"]

    def test_a_change_is_stored_and_reported_back(self, client, sudo_admin, certificates, idle_core):
        body = client.put(PATH, headers=auth(sudo_admin), json={"port": 8443}).json()

        assert body["port"] == 8443
        assert hysteria_settings_module.current().port == 8443

    def test_turning_it_on_starts_the_daemon(self, client, sudo_admin, certificates, idle_core):
        client.put(PATH, headers=auth(sudo_admin), json={"enabled": True})

        assert idle_core == ["start"]

    def test_turning_it_off_stops_the_daemon(self, client, sudo_admin, certificates, idle_core, db):
        hysteria_settings_module.save(db, enabled=True)

        client.put(PATH, headers=auth(sudo_admin), json={"enabled": False})

        assert idle_core == ["stop"]

    def test_a_refused_value_is_a_400_and_changes_nothing(self, client, sudo_admin, idle_core):
        response = client.put(PATH, headers=auth(sudo_admin), json={"masquerade_url": "ftp://x"})

        assert response.status_code == 400
        assert hysteria_settings_module.current().masquerade_url != "ftp://x"

    def test_a_cleared_domain_is_stored_as_none_rather_than_empty(
        self, client, sudo_admin, certificates, idle_core, db
    ):
        """The form sends "" for a cleared box, and "" would later be matched
        against certificate names as if a certificate were named nothing."""
        hysteria_settings_module.save(db, domain="vpn.example.com")

        client.put(PATH, headers=auth(sudo_admin), json={"domain": ""})

        assert hysteria_settings_module.current().domain is None

    def test_an_empty_body_changes_nothing(self, client, sudo_admin, certificates, idle_core, db):
        hysteria_settings_module.save(db, port=8443)

        body = client.put(PATH, headers=auth(sudo_admin), json={}).json()

        assert body["port"] == 8443
        assert idle_core == []

    def test_a_daemon_that_will_not_come_back_is_reported_not_raised(
        self, client, sudo_admin, monkeypatch, idle_core
    ):
        """The settings were saved. Refusing the request would say otherwise."""
        monkeypatch.setattr(certbot, "CERTBOT_ENABLED", False)

        response = client.put(PATH, headers=auth(sudo_admin), json={"enabled": True})

        assert response.status_code == 200
        assert response.json()["enabled"] is True
        assert "CERTBOT_ENABLED" in response.json()["reason"]


class TestAnUnmigratedDatabase:
    """A panel whose database has not had `alembic upgrade head` run on it.

    Found by driving the running panel rather than by reading the code: the
    read path already fell back to .env, so the screen looked fine and only
    saving blew up, with a traceback in the log and nothing useful on screen.
    """

    @pytest.fixture
    def without_the_table(self, db):
        HysteriaSettings.__table__.drop(db.get_bind())
        hysteria_settings_module.invalidate()
        yield
        HysteriaSettings.__table__.create(db.get_bind())

    def test_reading_falls_back_to_env_rather_than_failing(self, without_the_table):
        assert hysteria_settings_module.current().port == 443

    def test_saving_says_what_to_run(self, db, without_the_table):
        with pytest.raises(hysteria_settings_module.HysteriaSchemaError, match="alembic upgrade head"):
            hysteria_settings_module.save(db, port=8443)

    def test_the_api_answers_503_rather_than_500(self, client, sudo_admin, without_the_table):
        response = client.put(PATH, headers=auth(sudo_admin), json={"port": 8443})

        assert response.status_code == 503
        assert "alembic upgrade head" in response.json()["detail"]
