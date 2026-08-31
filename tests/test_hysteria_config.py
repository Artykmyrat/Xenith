"""The configuration the hysteria2 daemon is handed.

It is rewritten on every start, so the tests care about what it says rather
than about files persisting: which certificate it serves, where it sends the
daemon to check a password, and which of the optional parts are left out when
they are not configured.
"""

import os

import pytest
import yaml

from app.hysteria import config as hysteria
from app.hysteria.config import HysteriaConfigError
from app.utils import certbot
from app.utils.certbot import Certificate


def certificate(name="panel.example.com", domains=("panel.example.com",), paths=True):
    return Certificate(
        name=name,
        domains=list(domains),
        expires_at=None,
        certificate_path=f"/etc/letsencrypt/live/{name}/fullchain.pem" if paths else None,
        private_key_path=f"/etc/letsencrypt/live/{name}/privkey.pem" if paths else None,
    )


@pytest.fixture(autouse=True)
def certificates(monkeypatch):
    """One usable certificate, and the panel configured to have certbot."""
    monkeypatch.setattr(certbot, "CERTBOT_ENABLED", True)
    monkeypatch.setattr(certbot, "list_certificates", lambda: [certificate()])


@pytest.fixture(autouse=True)
def plain_settings(hysteria_settings, monkeypatch):
    """The defaults, so each test turns on only the thing it is about."""
    monkeypatch.setattr(hysteria, "UVICORN_SSL_CERTFILE", None)
    return hysteria_settings


class TestCertificate:
    def test_the_certificate_the_panel_holds_is_served(self):
        config = hysteria.render()

        assert config["tls"] == {
            "cert": "/etc/letsencrypt/live/panel.example.com/fullchain.pem",
            "key": "/etc/letsencrypt/live/panel.example.com/privkey.pem",
        }

    def test_a_named_domain_picks_its_certificate(self, hysteria_settings, monkeypatch):
        monkeypatch.setattr(
            certbot, "list_certificates", lambda: [certificate(), certificate("vpn.example.com", ["vpn.example.com"])]
        )
        hysteria_settings(domain="vpn.example.com")

        assert "vpn.example.com" in hysteria.render()["tls"]["cert"]

    def test_a_domain_no_certificate_covers_is_refused(self, hysteria_settings, monkeypatch):
        hysteria_settings(domain="other.example.com")

        with pytest.raises(HysteriaConfigError, match="other.example.com"):
            hysteria.render()

    def test_certificates_without_paths_do_not_count(self, monkeypatch):
        monkeypatch.setattr(certbot, "list_certificates", lambda: [certificate(paths=False)])

        with pytest.raises(HysteriaConfigError, match="Issue one"):
            hysteria.render()

    def test_it_says_what_to_do_when_certbot_is_off(self, monkeypatch):
        monkeypatch.setattr(certbot, "CERTBOT_ENABLED", False)

        with pytest.raises(HysteriaConfigError, match="CERTBOT_ENABLED"):
            hysteria.render()


class TestAuthentication:
    def test_the_daemon_asks_the_panel_over_loopback(self):
        http = hysteria.render()["auth"]["http"]

        assert http["url"] == f"http://127.0.0.1:{hysteria.UVICORN_PORT}/api/hysteria/auth"
        assert http["insecure"] is False

    def test_a_tls_panel_is_reached_over_tls_without_verifying_it(self, monkeypatch):
        # The panel's certificate is for its domain, not for 127.0.0.1, and this
        # request does not leave the machine.
        monkeypatch.setattr(hysteria, "UVICORN_SSL_CERTFILE", "/etc/letsencrypt/live/x/fullchain.pem")

        http = hysteria.render()["auth"]["http"]

        assert http["url"].startswith("https://127.0.0.1:")
        assert http["insecure"] is True


class TestOptionalParts:
    def test_obfuscation_is_left_out_until_it_has_a_password(self):
        assert "obfs" not in hysteria.render()

    def test_a_password_turns_salamander_on(self, hysteria_settings, monkeypatch):
        hysteria_settings(obfs_password="s3cret")

        assert hysteria.render()["obfs"] == {"type": "salamander", "salamander": {"password": "s3cret"}}

    def test_no_bandwidth_hint_by_default(self):
        assert "bandwidth" not in hysteria.render()

    def test_both_directions_are_needed_for_a_hint(self, hysteria_settings, monkeypatch):
        hysteria_settings(up_mbps=100)

        assert "bandwidth" not in hysteria.render()

    def test_a_full_pair_is_passed_through(self, hysteria_settings, monkeypatch):
        hysteria_settings(up_mbps=100, down_mbps=200)

        assert hysteria.render()["bandwidth"] == {"up": "100 mbps", "down": "200 mbps"}


class TestStatsAndMasquerade:
    def test_the_traffic_api_listens_on_loopback_only(self):
        assert hysteria.render()["trafficStats"]["listen"].startswith("127.0.0.1:")

    def test_the_traffic_api_carries_this_process_secret(self):
        assert hysteria.render()["trafficStats"]["secret"] == hysteria.STATS_SECRET
        assert len(hysteria.STATS_SECRET) >= 32

    def test_the_port_answers_as_a_website(self):
        assert hysteria.render()["masquerade"]["type"] == "proxy"


class TestWriting:
    def test_the_file_is_the_rendered_configuration(self, tmp_path):
        path = hysteria.write(str(tmp_path / "hysteria.yaml"))

        assert yaml.safe_load(open(path)) == hysteria.render()

    def test_the_file_keeps_its_secrets_to_itself(self, hysteria_settings, tmp_path, monkeypatch):
        hysteria_settings(obfs_password="s3cret")

        path = hysteria.write(str(tmp_path / "hysteria.yaml"))

        assert oct(os.stat(path).st_mode)[-3:] == "600"

    def test_a_directory_that_is_not_there_is_reported(self, tmp_path):
        with pytest.raises(HysteriaConfigError, match="does not exist"):
            hysteria.write(str(tmp_path / "nowhere" / "hysteria.yaml"))


class TestQuicWindows:
    """How far a receiver may run ahead of what it has acknowledged.

    On a link with a long round trip this, and not the bandwidth, is what caps
    the speed — so a server whose clients are a continent away is slow for a
    reason that has nothing to do with the pipe.
    """

    def test_the_windows_are_wider_than_the_daemon_would_pick(self):
        quic = hysteria.render()["quic"]

        # The daemon allows a stream 8 MB and a connection 20 MB by itself.
        assert quic["initStreamReceiveWindow"] > 8 * 1024 ** 2
        assert quic["initConnReceiveWindow"] > 20 * 1024 ** 2

    def test_a_window_starts_at_what_it_can_grow_to(self):
        quic = hysteria.render()["quic"]

        # A server with one client on another continent has no reason to
        # discover that slowly.
        assert quic["initStreamReceiveWindow"] == quic["maxStreamReceiveWindow"]
        assert quic["initConnReceiveWindow"] == quic["maxConnReceiveWindow"]

    def test_a_connection_is_allowed_more_than_one_of_its_streams(self, monkeypatch):
        quic = hysteria.render()["quic"]

        assert quic["initConnReceiveWindow"] > quic["initStreamReceiveWindow"]

    def test_megabytes_are_what_the_setting_means(self, monkeypatch):
        monkeypatch.setattr(hysteria, "HYSTERIA_QUIC_STREAM_WINDOW_MB", 3)

        assert hysteria.render()["quic"]["initStreamReceiveWindow"] == 3 * 1024 ** 2

    def test_one_half_can_be_left_to_the_daemon(self, monkeypatch):
        monkeypatch.setattr(hysteria, "HYSTERIA_QUIC_CONN_WINDOW_MB", 0)

        quic = hysteria.render()["quic"]

        assert "initStreamReceiveWindow" in quic
        assert "initConnReceiveWindow" not in quic

    def test_nothing_is_written_when_both_are_off(self, monkeypatch):
        monkeypatch.setattr(hysteria, "HYSTERIA_QUIC_STREAM_WINDOW_MB", 0)
        monkeypatch.setattr(hysteria, "HYSTERIA_QUIC_CONN_WINDOW_MB", 0)

        assert "quic" not in hysteria.render()

    def test_an_admin_who_writes_their_own_block_gets_it(self, hysteria_settings):
        mine = {"maxIdleTimeout": "60s", "initConnReceiveWindow": 1024}
        hysteria_settings(extra={"quic": mine})

        assert hysteria.render()["quic"] == mine
