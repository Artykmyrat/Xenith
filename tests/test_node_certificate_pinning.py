import ssl

import pytest
from OpenSSL import crypto

from app.xray.node import (
    NodeCertificateMismatch,
    ReSTXRayNode,
    certificate_fingerprint,
)


def make_certificate(common_name: str = "Gozargah"):
    """A throwaway self-signed certificate, standing in for a node's own."""
    key = crypto.PKey()
    key.generate_key(crypto.TYPE_RSA, 2048)
    cert = crypto.X509()
    cert.get_subject().CN = common_name
    cert.set_serial_number(1)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(3600)
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(key)
    cert.sign(key, "sha256")
    return (
        crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode(),
        crypto.dump_privatekey(crypto.FILETYPE_PEM, key).decode(),
    )


@pytest.fixture(scope="module")
def client_identity():
    return make_certificate("client")


@pytest.fixture(scope="module")
def node_cert():
    return make_certificate()[0]


@pytest.fixture(scope="module")
def other_node_cert():
    return make_certificate()[0]


@pytest.fixture
def node(client_identity, monkeypatch):
    """Build a node whose network calls are stubbed out."""
    cert, key = client_identity

    def build(server_cert=None):
        instance = ReSTXRayNode(
            address="203.0.113.10", port=62050, api_port=62051,
            ssl_key=key, ssl_cert=cert, server_cert=server_cert,
        )
        monkeypatch.setattr(
            type(instance), "make_request",
            lambda self, path, timeout, **kw: {"session_id": "stub"},
        )
        return instance

    return build


def present(monkeypatch, pem):
    monkeypatch.setattr(ssl, "get_server_certificate", lambda addr: pem)


class TestFingerprint:
    def test_is_stable_for_the_same_certificate(self, node_cert):
        assert certificate_fingerprint(node_cert) == certificate_fingerprint(node_cert)

    def test_differs_between_certificates(self, node_cert, other_node_cert):
        assert certificate_fingerprint(node_cert) != certificate_fingerprint(other_node_cert)


class TestPinning:
    def test_first_connection_pins_the_presented_certificate(self, node, node_cert, monkeypatch):
        instance = node()
        assert instance.server_cert is None

        present(monkeypatch, node_cert)
        instance.connect()

        assert instance.server_cert == node_cert

    def test_matching_certificate_is_accepted(self, node, node_cert, monkeypatch):
        instance = node(server_cert=node_cert)
        present(monkeypatch, node_cert)

        instance.connect()

        assert instance.server_cert == node_cert

    def test_a_different_certificate_is_refused(self, node, node_cert, other_node_cert, monkeypatch):
        instance = node(server_cert=node_cert)
        present(monkeypatch, other_node_cert)

        with pytest.raises(NodeCertificateMismatch):
            instance.connect()

    def test_the_pin_survives_a_refused_connection(self, node, node_cert, other_node_cert, monkeypatch):
        instance = node(server_cert=node_cert)
        present(monkeypatch, other_node_cert)

        with pytest.raises(NodeCertificateMismatch):
            instance.connect()

        assert instance.server_cert == node_cert


class TestTLSContext:
    def test_certificates_are_verified(self, node):
        assert node()._ssl_context.verify_mode == ssl.CERT_REQUIRED

    def test_hostname_checking_stays_off(self, node):
        # Node certificates carry a fixed CN, not the node's address.
        assert node()._ssl_context.check_hostname is False

    def test_a_known_certificate_is_used_as_the_trust_anchor(self, node, node_cert):
        instance = node(server_cert=node_cert)

        with open(instance.session.verify) as bundle:
            assert bundle.read() == node_cert
