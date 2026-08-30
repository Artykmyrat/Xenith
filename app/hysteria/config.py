"""The configuration the hysteria2 daemon is started with.

Rendered from the panel's settings on every start rather than kept as a file an
admin edits: what the daemon runs then always matches what the panel believes
it runs, and there is one place — .env — where the answer lives.

Three parts are decisions rather than plumbing, and each is written down where
it is made: the certificate comes from certbot, so there is no self-signed
detour and no client told to skip verification; authentication is a callback to
the panel, so adding or suspending a user restarts nothing; and the traffic API
listens on loopback with a secret this process generates, because it is
answerable to nobody else.
"""

import secrets
from typing import Dict, Optional

import yaml

from app.utils import certbot
from config import (HYSTERIA_CONFIG_PATH, HYSTERIA_DOMAIN, HYSTERIA_DOWN_MBPS,
                    HYSTERIA_MASQUERADE_URL, HYSTERIA_OBFS_PASSWORD,
                    HYSTERIA_PORT, HYSTERIA_STATS_PORT, HYSTERIA_UP_MBPS,
                    UVICORN_PORT, UVICORN_SSL_CERTFILE)

# Regenerated per process. The daemon is handed it in its configuration and the
# panel keeps it in memory, so a restart of either invalidates nothing that
# outlives them both.
STATS_SECRET = secrets.token_hex(16)

AUTH_PATH = "/api/hysteria/auth"


class HysteriaConfigError(Exception):
    """The daemon cannot be configured; the message is safe to show."""


def _certificate() -> tuple:
    """The certificate the daemon presents: (certificate path, key path).

    Hysteria2 is QUIC with real TLS, so it needs a certificate a client will
    accept. The panel already has certbot for that, and the alternative — a
    self-signed pair plus `insecure` on every client — is the thing that makes
    a hysteria deployment trivially fingerprintable.
    """
    if not certbot.is_enabled():
        raise HysteriaConfigError(
            "Hysteria2 needs a TLS certificate, and certificate management is off. "
            "Set CERTBOT_ENABLED and issue one on the Certificates screen."
        )

    try:
        certificates = certbot.list_certificates()
    except certbot.CertbotError as err:
        raise HysteriaConfigError(str(err))

    usable = [c for c in certificates if c.certificate_path and c.private_key_path]
    if not usable:
        raise HysteriaConfigError(
            "Hysteria2 needs a TLS certificate. Issue one on the Certificates screen."
        )

    if HYSTERIA_DOMAIN:
        for certificate in usable:
            if HYSTERIA_DOMAIN == certificate.name or HYSTERIA_DOMAIN in certificate.domains:
                return certificate.certificate_path, certificate.private_key_path
        raise HysteriaConfigError(
            f"No certificate covers {HYSTERIA_DOMAIN}. Issue one, or clear HYSTERIA_DOMAIN "
            "to use the certificate the panel already holds."
        )

    certificate = usable[0]
    return certificate.certificate_path, certificate.private_key_path


def auth_url() -> str:
    """Where the daemon asks whether a password belongs to a live user.

    Loopback, because the endpoint has no admin token to check and is trusted
    on the strength of where the request came from. The scheme follows the
    panel: when it serves TLS, the certificate is for the domain rather than
    for 127.0.0.1, so verification is turned off for this one hop — it never
    leaves the machine.
    """
    scheme = "https" if UVICORN_SSL_CERTFILE else "http"
    return f"{scheme}://127.0.0.1:{UVICORN_PORT}{AUTH_PATH}"


def render() -> Dict:
    """The daemon's configuration, as the structure that becomes its YAML."""
    certificate_path, key_path = _certificate()

    config: Dict = {
        "listen": f":{HYSTERIA_PORT}",
        "tls": {"cert": certificate_path, "key": key_path},
        "auth": {
            "type": "http",
            "http": {"url": auth_url(), "insecure": bool(UVICORN_SSL_CERTFILE)},
        },
        # Polled for usage; `clear=1` on read is what makes the counters
        # deltas, which is what the panel records.
        "trafficStats": {"listen": f"127.0.0.1:{HYSTERIA_STATS_PORT}", "secret": STATS_SECRET},
        # A port that answers like a website is a port that looks like one.
        "masquerade": {
            "type": "proxy",
            "proxy": {"url": HYSTERIA_MASQUERADE_URL, "rewriteHost": True},
        },
    }

    if HYSTERIA_OBFS_PASSWORD:
        config["obfs"] = {
            "type": "salamander",
            "salamander": {"password": HYSTERIA_OBFS_PASSWORD},
        }

    # Both or neither: hysteria reads one missing side as unlimited, and a
    # half-filled pair is a slower tunnel than none at all.
    if HYSTERIA_UP_MBPS and HYSTERIA_DOWN_MBPS:
        config["bandwidth"] = {
            "up": f"{HYSTERIA_UP_MBPS} mbps",
            "down": f"{HYSTERIA_DOWN_MBPS} mbps",
        }

    return config


def write(path: Optional[str] = None) -> str:
    """Render the configuration to disk and return where it was written."""
    from app.utils.files import FileWriteError, atomic_write

    path = path or HYSTERIA_CONFIG_PATH
    body = yaml.safe_dump(render(), sort_keys=False, default_flow_style=False)

    try:
        # 0600: the file carries the obfuscation password and the stats secret.
        atomic_write(path, body, mode=0o600)
    except FileWriteError as err:
        raise HysteriaConfigError(str(err))

    return path
