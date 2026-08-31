"""Ready-made inbounds for the core configuration screen.

The point of a template is that it can be saved without editing, so the tests
care most about that: what comes out has to survive the panel's own parser,
and it has to avoid the tags and ports the configuration already uses.
"""

import json

import pytest

from app.utils import certbot, inbound_template
from app.utils.certbot import Certificate
from app.utils.crypto import generate_x25519_keypair, get_x25519_public_key
from app.utils.inbound_template import TemplateError
from app.xray import XRayConfig

from conftest import auth

BASE_CONFIG = {
    "log": {"loglevel": "warning"},
    "inbounds": [],
    "outbounds": [{"protocol": "freedom", "tag": "DIRECT"}],
}


@pytest.fixture
def certificates(monkeypatch, tmp_path):
    """One usable certificate, written where the parser can read it."""
    from app.utils.crypto import generate_certificate

    pair = generate_certificate()
    cert_path = tmp_path / "fullchain.pem"
    key_path = tmp_path / "privkey.pem"
    cert_path.write_text(pair["cert"])
    key_path.write_text(pair["key"])

    certificate = Certificate(
        name="panel.example.com",
        domains=["panel.example.com"],
        expires_at=None,
        certificate_path=str(cert_path),
        private_key_path=str(key_path),
    )

    monkeypatch.setattr(certbot, "CERTBOT_ENABLED", True)
    monkeypatch.setattr(certbot, "list_certificates", lambda: [certificate])
    return certificate


def parse(*inbounds) -> XRayConfig:
    config = json.loads(json.dumps(BASE_CONFIG))
    config["inbounds"].extend(inbounds)
    return XRayConfig(config, api_port=8080)


class TestKeyPair:
    def test_the_public_key_belongs_to_the_private_one(self):
        private_key, public_key = generate_x25519_keypair()

        assert get_x25519_public_key(private_key) == public_key

    def test_keys_carry_no_padding(self):
        private_key, public_key = generate_x25519_keypair()

        assert "=" not in private_key and "=" not in public_key

    def test_every_pair_is_new(self):
        assert generate_x25519_keypair()[0] != generate_x25519_keypair()[0]


class TestChoices:
    @pytest.mark.parametrize("transport", ["tcp", "grpc", "xhttp"])
    def test_reality_is_built_for_the_transports_that_support_it(self, transport):
        inbound = inbound_template.build(transport, "reality")

        assert inbound["streamSettings"]["security"] == "reality"
        assert inbound["streamSettings"]["network"] == transport

    def test_reality_over_websocket_is_refused(self):
        with pytest.raises(TemplateError, match="WebSocket"):
            inbound_template.build("ws", "reality")

    def test_an_unknown_transport_is_refused(self):
        with pytest.raises(TemplateError, match="transport"):
            inbound_template.build("quic", "reality")

    def test_an_unknown_security_is_refused(self):
        with pytest.raises(TemplateError, match="security"):
            inbound_template.build("tcp", "none")


class TestRealityTemplate:
    def test_it_parses_as_the_panel_reads_it(self):
        inbound = inbound_template.build("tcp", "reality")

        settings = parse(inbound).inbounds_by_tag[inbound["tag"]]

        reality = inbound["streamSettings"]["realitySettings"]
        assert settings["tls"] == "reality"
        assert settings["sni"] == reality["serverNames"]
        assert reality["dest"] == f"{reality['serverNames'][0]}:443"
        assert settings["sids"] == reality["shortIds"]

    def test_the_public_key_the_panel_derives_matches_the_private_one(self):
        inbound = inbound_template.build("tcp", "reality")
        private_key = inbound["streamSettings"]["realitySettings"]["privateKey"]

        settings = parse(inbound).inbounds_by_tag[inbound["tag"]]

        assert settings["pbk"] == get_x25519_public_key(private_key)

    def test_the_xhttp_path_and_mode_reach_the_panel(self):
        inbound = inbound_template.build("xhttp", "reality")

        settings = parse(inbound).inbounds_by_tag[inbound["tag"]]

        assert settings["network"] == "xhttp"
        assert settings["path"] == inbound["streamSettings"]["xhttpSettings"]["path"]
        # One request in both directions: nothing else is opened before the
        # stream is up. A CDN in front would refuse it; there is none here.
        assert settings["mode"] == "stream-one"

    def test_xhttp_carries_connection_reuse_to_the_client(self):
        """Without xmux every new stream repeats the whole handshake."""
        inbound = inbound_template.build("xhttp", "reality")

        settings = parse(inbound).inbounds_by_tag[inbound["tag"]]

        assert settings["xmux"] == inbound_template.XHTTP_XMUX
        assert settings["xmux"]["maxConcurrency"] == "16-32"
        assert settings["scMaxEachPostBytes"] == 1000000
        assert settings["scMinPostsIntervalMs"] == 10

    def test_the_grpc_service_name_reaches_the_panel(self):
        inbound = inbound_template.build("grpc", "reality")

        settings = parse(inbound).inbounds_by_tag[inbound["tag"]]

        assert settings["path"] == inbound["streamSettings"]["grpcSettings"]["serviceName"]

    def test_each_template_carries_its_own_secrets(self):
        first = inbound_template.build("tcp", "reality")["streamSettings"]["realitySettings"]
        second = inbound_template.build("tcp", "reality")["streamSettings"]["realitySettings"]

        assert first["privateKey"] != second["privateKey"]
        assert first["shortIds"] != second["shortIds"]


class TestTlsTemplate:
    def test_it_serves_the_certificate_certbot_holds(self, certificates):
        inbound = inbound_template.build("ws", "tls")

        tls = inbound["streamSettings"]["tlsSettings"]
        assert tls["serverName"] == certificates.domains[0]
        assert tls["certificates"] == [
            {
                "certificateFile": certificates.certificate_path,
                "keyFile": certificates.private_key_path,
            }
        ]

    @pytest.mark.parametrize("transport", ["tcp", "grpc", "ws", "xhttp"])
    def test_every_transport_is_offered_with_tls(self, certificates, transport):
        inbound = inbound_template.build(transport, "tls")

        settings = parse(inbound).inbounds_by_tag[inbound["tag"]]

        assert settings["tls"] == "tls"
        assert settings["network"] == transport

    def test_it_parses_as_the_panel_reads_it(self, certificates):
        inbound = inbound_template.build("ws", "tls")

        settings = parse(inbound).inbounds_by_tag[inbound["tag"]]

        assert settings["tls"] == "tls"
        assert settings["network"] == "ws"
        assert settings["path"] == inbound["streamSettings"]["wsSettings"]["path"]

    def test_a_certificate_with_no_domains_listed_falls_back_to_its_name(self, certificates, monkeypatch):
        nameless = Certificate(
            name="panel.example.com", domains=[], expires_at=None,
            certificate_path=certificates.certificate_path,
            private_key_path=certificates.private_key_path,
        )
        monkeypatch.setattr(certbot, "list_certificates", lambda: [nameless])

        inbound = inbound_template.build("tcp", "tls")

        assert inbound["streamSettings"]["tlsSettings"]["serverName"] == "panel.example.com"

    def test_a_certificate_without_paths_is_not_offered(self, certificates, monkeypatch):
        unusable = Certificate(
            name="half", domains=["half.example.com"], expires_at=None,
            certificate_path=None, private_key_path=None,
        )
        monkeypatch.setattr(certbot, "list_certificates", lambda: [unusable])

        with pytest.raises(TemplateError, match="Issue one"):
            inbound_template.build("tcp", "tls")

    def test_tls_says_what_to_do_when_certificates_are_off(self, monkeypatch):
        monkeypatch.setattr(certbot, "CERTBOT_ENABLED", False)

        with pytest.raises(TemplateError, match="CERTBOT_ENABLED"):
            inbound_template.build("tcp", "tls")


class TestPlacement:
    def test_a_taken_tag_gets_a_suffix(self):
        inbound = inbound_template.build("tcp", "reality", taken_tags=["VLESS TCP REALITY"])

        assert inbound["tag"] == "VLESS TCP REALITY 2"

    def test_taken_ports_are_stepped_over(self):
        first = inbound_template.FIRST_PORT

        inbound = inbound_template.build("tcp", "reality", taken_ports=[first, first + 1])

        assert inbound["port"] == first + 2

    def test_the_panel_keeps_its_own_port(self, monkeypatch):
        monkeypatch.setattr(inbound_template, "UVICORN_PORT", inbound_template.FIRST_PORT)

        inbound = inbound_template.build("tcp", "reality")

        assert inbound["port"] != inbound_template.FIRST_PORT

    def test_two_templates_can_be_added_one_after_another(self):
        first = inbound_template.build("tcp", "reality")
        second = inbound_template.build("grpc", "reality", [first["tag"]], [first["port"]])

        config = parse(first, second)

        assert set(config.inbounds_by_tag) == {first["tag"], second["tag"]}


class TestEndpoint:
    PATH = "/api/core/inbound-template"

    def test_no_credentials_is_rejected(self, client):
        assert client.post(self.PATH, json={"transport": "tcp", "security": "reality"}).status_code == 401

    def test_a_reseller_is_rejected(self, client, plain_admin):
        response = client.post(
            self.PATH, json={"transport": "tcp", "security": "reality"}, headers=auth(plain_admin)
        )

        assert response.status_code == 403

    def test_a_sudo_admin_gets_an_inbound(self, client, sudo_admin):
        response = client.post(
            self.PATH,
            json={"transport": "tcp", "security": "reality", "taken_tags": ["VLESS TCP REALITY"]},
            headers=auth(sudo_admin),
        )

        assert response.status_code == 200
        assert response.json()["tag"] == "VLESS TCP REALITY 2"

    def test_an_impossible_combination_is_explained(self, client, sudo_admin):
        response = client.post(
            self.PATH, json={"transport": "ws", "security": "reality"}, headers=auth(sudo_admin)
        )

        assert response.status_code == 400
        assert "WebSocket" in response.json()["detail"]

    def test_a_transport_outside_the_list_is_refused(self, client, sudo_admin):
        response = client.post(
            self.PATH, json={"transport": "quic", "security": "tls"}, headers=auth(sudo_admin)
        )

        assert response.status_code == 422


class TestDest:
    """Which host REALITY borrows its handshake from.

    The server relays every incoming handshake into `dest` before it knows
    whether the client is one of ours, so that round trip is spent on each
    connection a client makes — it is most of what a client measures as the
    time it took to connect.
    """

    @pytest.fixture(autouse=True)
    def unmeasured(self, monkeypatch):
        """No cached measurement, and no probing unless a test asks for it."""
        monkeypatch.setattr(inbound_template, "_dest_cache", None)
        monkeypatch.setattr(inbound_template, "XRAY_REALITY_DEST", "")
        monkeypatch.setattr(inbound_template, "_connect_seconds", lambda host: None)

    def probe(self, monkeypatch, timings):
        monkeypatch.setattr(
            inbound_template, "_connect_seconds", lambda host: timings.get(host)
        )

    def test_the_nearest_candidate_wins(self, monkeypatch):
        self.probe(monkeypatch, {"www.apple.com": 0.004, "www.microsoft.com": 0.180})

        assert inbound_template.choose_dest() == "www.apple.com"

    def test_a_candidate_that_does_not_answer_is_passed_over(self, monkeypatch):
        # The fastest of the two answers, not the first one asked.
        self.probe(monkeypatch, {"dl.google.com": 0.020, "www.bing.com": 0.300})

        assert inbound_template.choose_dest() == "dl.google.com"

    def test_a_server_that_reaches_nothing_keeps_the_old_default(self):
        # Every probe returns None here, which is what a firewalled server
        # looks like. An unreachable dest would be a broken inbound.
        assert inbound_template.choose_dest() == inbound_template.REALITY_DEST

    def test_the_measurement_is_not_repeated_for_every_template(self, monkeypatch):
        probed = []

        def count(host):
            probed.append(host)
            return 0.01

        monkeypatch.setattr(inbound_template, "_connect_seconds", count)

        inbound_template.choose_dest()
        seen = len(probed)
        inbound_template.choose_dest()

        assert len(probed) == seen

    def test_a_configured_dest_is_used_without_measuring(self, monkeypatch):
        monkeypatch.setattr(inbound_template, "XRAY_REALITY_DEST", "localhost")

        def refuse(host):
            raise AssertionError("a configured dest should not be measured")

        monkeypatch.setattr(inbound_template, "_connect_seconds", refuse)

        assert inbound_template.choose_dest() == "localhost"

    def test_the_chosen_dest_is_what_the_template_carries(self, monkeypatch):
        self.probe(monkeypatch, {"www.cloudflare.com": 0.003})

        reality = inbound_template.build("tcp", "reality")["streamSettings"]["realitySettings"]

        assert reality["serverNames"] == ["www.cloudflare.com"]
        assert reality["dest"] == "www.cloudflare.com:443"


class TestDialTuning:
    """Settings that cost a round trip when they are missing."""

    def test_sniffing_does_not_send_the_core_back_to_dns(self):
        inbound = inbound_template.build("tcp", "reality")

        # destOverride alone throws away the address the client resolved and
        # makes the core look the domain up again, once per connection.
        assert inbound["sniffing"]["routeOnly"] is True

    @pytest.mark.parametrize("transport", ["tcp", "grpc", "ws", "xhttp"])
    def test_every_transport_asks_for_fast_open(self, certificates, transport):
        inbound = inbound_template.build(transport, "tls")

        assert inbound["streamSettings"]["sockopt"]["tcpFastOpen"] is True

    def test_the_tuning_survives_the_panel_s_own_parser(self):
        inbound = inbound_template.build("xhttp", "reality")

        parse(inbound)  # raises if the panel would refuse the template


class TestXhttpShape:
    """Where the core looks for XHTTP settings, and where the panel looks.

    Only host, path and mode belong at the top of `xhttpSettings`; the core
    reads the rest out of `extra`. The panel reads both, so an inbound written
    by hand before that is still understood.
    """

    def test_the_tuning_is_written_where_the_core_reads_it(self):
        xhttp = inbound_template.build("xhttp", "reality")["streamSettings"]["xhttpSettings"]

        assert set(xhttp) == {"path", "mode", "extra"}
        assert xhttp["extra"]["xmux"] == inbound_template.XHTTP_XMUX

    def test_the_panel_reads_the_tuning_back_out_of_extra(self):
        inbound = inbound_template.build("xhttp", "reality")

        settings = parse(inbound).inbounds_by_tag[inbound["tag"]]

        assert settings["xmux"] == inbound_template.XHTTP_XMUX
        assert settings["scMinPostsIntervalMs"] == 10
        assert settings["mode"] == "stream-one"

    def test_an_inbound_written_flat_is_still_understood(self):
        """The shape admins wrote before `extra` existed."""
        inbound = inbound_template.build("xhttp", "reality")
        xhttp = inbound["streamSettings"]["xhttpSettings"]
        xhttp.update(xhttp.pop("extra"))

        settings = parse(inbound).inbounds_by_tag[inbound["tag"]]

        assert settings["xmux"] == inbound_template.XHTTP_XMUX
        assert settings["scMinPostsIntervalMs"] == 10

    def test_extra_wins_over_a_stale_flat_copy(self):
        inbound = inbound_template.build("xhttp", "reality")
        xhttp = inbound["streamSettings"]["xhttpSettings"]
        xhttp["scMinPostsIntervalMs"] = 30

        settings = parse(inbound).inbounds_by_tag[inbound["tag"]]

        assert settings["scMinPostsIntervalMs"] == 10
