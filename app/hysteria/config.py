"""The configuration the hysteria2 daemon is started with.

Rendered from the panel's settings on every start rather than kept as a file an
admin edits: what the daemon runs then always matches what the panel believes
it runs, and there is one place — app/hysteria/settings.py — where the answer
lives.

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

from app.hysteria import settings as hysteria_settings
from app.utils import certbot
from config import (HYSTERIA_CONFIG_PATH, HYSTERIA_QUIC_CONN_WINDOW_MB,
                    HYSTERIA_QUIC_STREAM_WINDOW_MB, UVICORN_PORT,
                    UVICORN_SSL_CERTFILE)

MEGABYTE = 1024 ** 2

# Regenerated per process. The daemon is handed it in its configuration and the
# panel keeps it in memory, so a restart of either invalidates nothing that
# outlives them both.
STATS_SECRET = secrets.token_hex(16)

AUTH_PATH = "/api/hysteria/auth"


class HysteriaConfigError(Exception):
    """The daemon cannot be configured; the message is safe to show."""


def _certificate(domain: Optional[str] = None) -> tuple:
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

    if domain:
        for certificate in usable:
            if domain == certificate.name or domain in certificate.domains:
                return certificate.certificate_path, certificate.private_key_path
        raise HysteriaConfigError(
            f"No certificate covers {domain}. Issue one, or clear the domain on the Core "
            "screen to use the certificate the panel already holds."
        )

    certificate = usable[0]
    return certificate.certificate_path, certificate.private_key_path


def _quic() -> Dict:
    """The flow-control windows the daemon's QUIC connections are given.

    A receiver may only run this far ahead of what it has acknowledged, so on
    a link with a long round trip the window is what caps the speed rather
    than the bandwidth: 20 MB — what the daemon allows a connection by itself
    — over 200 ms comes to about 800 Mbps, however wide the pipe is. The
    windows are set to what they can grow to from the start, since a server
    that has one client on another continent has no reason to discover that
    slowly.

    The cost is memory, and only for data actually in flight, so it is paid by
    the clients fast enough to fill a window rather than by every connected
    one. Either half can be set to 0 to leave the daemon's own default in
    place, and an admin who writes a `quic` block into `extra` replaces this
    entirely.
    """
    quic: Dict = {}

    if HYSTERIA_QUIC_STREAM_WINDOW_MB > 0:
        window = HYSTERIA_QUIC_STREAM_WINDOW_MB * MEGABYTE
        quic["initStreamReceiveWindow"] = window
        quic["maxStreamReceiveWindow"] = window

    if HYSTERIA_QUIC_CONN_WINDOW_MB > 0:
        window = HYSTERIA_QUIC_CONN_WINDOW_MB * MEGABYTE
        quic["initConnReceiveWindow"] = window
        quic["maxConnReceiveWindow"] = window

    return quic


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


def render(settings=None) -> Dict:
    """The daemon's configuration, as the structure that becomes its YAML."""
    settings = settings or hysteria_settings.current()
    certificate_path, key_path = _certificate(settings.domain)

    config: Dict = {
        "listen": f":{settings.port}",
        "tls": {"cert": certificate_path, "key": key_path},
        "auth": {
            "type": "http",
            "http": {"url": auth_url(), "insecure": bool(UVICORN_SSL_CERTFILE)},
        },
        # Polled for usage; `clear=1` on read is what makes the counters
        # deltas, which is what the panel records.
        "trafficStats": {"listen": f"127.0.0.1:{settings.stats_port}", "secret": STATS_SECRET},
    }

    if quic := _quic():
        config["quic"] = quic

    # A port that answers like a website is a port that looks like one. Left
    # out entirely when there is no URL, rather than pointed at nothing.
    if settings.masquerade_url:
        config["masquerade"] = {
            "type": "proxy",
            "proxy": {"url": settings.masquerade_url, "rewriteHost": True},
        }

    if settings.obfs_password:
        config["obfs"] = {
            "type": "salamander",
            "salamander": {"password": settings.obfs_password},
        }

    # Both or neither: hysteria reads one missing side as unlimited, and a
    # half-filled pair is a slower tunnel than none at all.
    if settings.up_mbps and settings.down_mbps:
        config["bandwidth"] = {
            "up": f"{settings.up_mbps} mbps",
            "down": f"{settings.down_mbps} mbps",
        }

    # Merged last, and only over keys the panel does not own — the settings
    # module refuses the reserved ones before they are ever stored, so by the
    # time anything reaches here there is nothing left to guard against.
    for key, value in (settings.extra or {}).items():
        if key not in hysteria_settings.RESERVED_KEYS:
            config[key] = value

    return config


def preview(settings=None) -> str:
    """The rendered file as text, for the panel to show.

    The stats secret is the one thing held back: it is this process's key to
    its own daemon, it is regenerated on every start, and an admin reading the
    screen has no use for it.
    """
    config = render(settings)
    config = {
        **config,
        "trafficStats": {**config["trafficStats"], "secret": "<generated on each start>"},
    }
    return yaml.safe_dump(config, sort_keys=False, default_flow_style=False)


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
