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

        assert settings["tls"] == "reality"
        assert settings["sni"] == [inbound_template.REALITY_DEST]
        assert settings["sids"] == inbound["streamSettings"]["realitySettings"]["shortIds"]

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
        # "auto" leaves the choice of packet-up, stream-up or stream-one to the client.
        assert settings["mode"] == "auto"

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
