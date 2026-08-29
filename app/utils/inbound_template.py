"""Ready-made inbounds for the core configuration screen.

What comes out is meant to be dropped into `inbounds` and saved without
further editing, so each template carries everything the panel demands of it:
a REALITY inbound gets a freshly generated key pair and a short ID, a TLS one
points at a certificate certbot already holds, and both get a tag and a port
the configuration is not already using.

Only the three transports the buttons offer are built here. REALITY is not
among xray's options for WebSocket — it needs the raw stream that tcp and grpc
give it — so that one combination is refused rather than quietly downgraded.
"""

import secrets
from typing import Dict, Iterable, List, Optional, Tuple

from app.utils import certbot
from app.utils.crypto import generate_x25519_keypair
from config import UVICORN_PORT

TRANSPORTS = ("tcp", "grpc", "ws")
SECURITIES = ("tls", "reality")

# Where REALITY sends traffic that is not one of ours. A well-known host that
# speaks TLSv1.3 and HTTP/2, which is what the handshake has to borrow.
REALITY_DEST = "www.microsoft.com"

# Above the range a panel is likely to have taken, and clear of the privileged
# ports, so the first suggestion is usually the one that gets kept.
FIRST_PORT = 8443
LAST_PORT = 65535


class TemplateError(Exception):
    """The template could not be built; the message is safe to show."""


def _unique_tag(base: str, taken: Iterable[str]) -> str:
    taken = set(taken)
    if base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base} {suffix}"
        if candidate not in taken:
            return candidate
    raise TemplateError(f"Too many inbounds are already called {base!r}.")


def _free_port(taken: Iterable[int]) -> int:
    """The lowest unused port at or above FIRST_PORT.

    The panel's own port is treated as taken: the two would bind the same
    socket, and the core is the one that would lose.
    """
    taken = {port for port in taken if isinstance(port, int)}
    taken.add(int(UVICORN_PORT))
    for port in range(FIRST_PORT, LAST_PORT + 1):
        if port not in taken:
            return port
    raise TemplateError("No free port is left above %d." % FIRST_PORT)


def _certificate() -> Tuple[str, str, str]:
    """The certificate a TLS inbound should present: (domain, cert, key).

    The panel reads the certificate file while it parses the configuration, so
    a template that named a file that is not there would be refused on save.
    """
    if not certbot.is_enabled():
        raise TemplateError(
            "TLS needs a certificate, and certificate management is off. "
            "Set CERTBOT_ENABLED, or write the tlsSettings by hand."
        )

    try:
        certificates = certbot.list_certificates()
    except certbot.CertbotError as err:
        raise TemplateError(str(err))

    for certificate in certificates:
        if certificate.certificate_path and certificate.private_key_path and certificate.domains:
            return certificate.domains[0], certificate.certificate_path, certificate.private_key_path

    raise TemplateError(
        "No certificate to serve. Issue one on the Certificates screen first, "
        "or use REALITY, which needs none."
    )


def _stream(transport: str, security: str) -> Dict:
    """The transport half of streamSettings, with a path nobody can guess."""
    if transport == "tcp":
        return {"network": "tcp", "security": security}

    if transport == "grpc":
        return {
            "network": "grpc",
            "security": security,
            "grpcSettings": {"serviceName": secrets.token_hex(4), "multiMode": False},
        }

    return {
        "network": "ws",
        "security": security,
        "wsSettings": {"path": f"/{secrets.token_hex(4)}"},
    }


def _security(security: str) -> Dict:
    """The security half: either someone else's handshake, or our own."""
    if security == "reality":
        private_key, _ = generate_x25519_keypair()
        return {
            "realitySettings": {
                "show": False,
                "dest": f"{REALITY_DEST}:443",
                "xver": 0,
                "serverNames": [REALITY_DEST],
                "privateKey": private_key,
                # One is all the panel asks for, and one is all a client uses.
                "shortIds": [secrets.token_hex(4)],
            }
        }

    domain, certificate_path, private_key_path = _certificate()
    return {
        "tlsSettings": {
            "serverName": domain,
            "certificates": [{"certificateFile": certificate_path, "keyFile": private_key_path}],
        }
    }


def build(
    transport: str,
    security: str,
    taken_tags: Optional[List[str]] = None,
    taken_ports: Optional[List[int]] = None,
) -> Dict:
    """One inbound, ready to be appended to the configuration."""
    if transport not in TRANSPORTS:
        raise TemplateError(f"{transport!r} is not a transport this offers.")
    if security not in SECURITIES:
        raise TemplateError(f"{security!r} is not a security this offers.")
    if security == "reality" and transport == "ws":
        raise TemplateError(
            "REALITY does not work over WebSocket. Use tcp or grpc for REALITY, "
            "or TLS for WebSocket."
        )

    stream = _stream(transport, security)
    stream.update(_security(security))

    return {
        "tag": _unique_tag(f"VLESS {transport.upper()} {security.upper()}", taken_tags or []),
        "listen": "0.0.0.0",
        "port": _free_port(taken_ports or []),
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": stream,
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    }
