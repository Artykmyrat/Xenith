"""Ready-made inbounds for the core configuration screen.

What comes out is meant to be dropped into `inbounds` and saved without
further editing, so each template carries everything the panel demands of it:
a REALITY inbound gets a freshly generated key pair and a short ID, a TLS one
points at a certificate certbot already holds, and both get a tag and a port
the configuration is not already using.

Only the transports the buttons offer are built here. REALITY is not among
xray's options for WebSocket — it needs the raw stream that the others give it
— so that one combination is refused rather than quietly downgraded.

Templates are also written for how long a client waits before its first byte
moves. Three things dominate that wait, and all three are decided here rather
than by the client: which host REALITY borrows its handshake from, whether a
second connection has to repeat that handshake at all, and whether the core
looks a domain up in DNS before it dials out. See `choose_dest`, `XHTTP_XMUX`
and the `sniffing` block below.
"""

import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional, Tuple

from app.utils import certbot
from app.utils.crypto import generate_x25519_keypair
from config import UVICORN_PORT, XRAY_REALITY_DEST

TRANSPORTS = ("tcp", "grpc", "ws", "xhttp")
SECURITIES = ("tls", "reality")

# Where REALITY sends traffic that is not one of ours. A well-known host that
# speaks TLSv1.3 and HTTP/2, which is what the handshake has to borrow.
REALITY_DEST = "www.microsoft.com"

# A REALITY server opens a connection to `dest` and relays the client's
# handshake into it before deciding whether the client is one of ours, so the
# round trip to that host is paid on every new connection — not once. A host
# that answers in 5 ms and one that answers in 200 ms therefore differ by
# roughly that much on each connect, which is most of what a client measures
# as "time to connect".
#
# Every candidate speaks TLSv1.3 and HTTP/2 and sits behind a CDN with enough
# points of presence that at least one is usually near the server. The list is
# ordered only for readability; which one a server ends up on is measured.
REALITY_DEST_CANDIDATES: Tuple[str, ...] = (
    "www.microsoft.com",
    "www.apple.com",
    "www.cloudflare.com",
    "dl.google.com",
    "www.bing.com",
    "aws.amazon.com",
    "cdn.jsdelivr.net",
    "www.samsung.com",
)

# A candidate that has not answered by then is not the fastest one anyway.
DEST_PROBE_TIMEOUT = 1.0

# The measurement is about where the server sits, which does not change
# between two clicks of the same button.
DEST_CACHE_SECONDS = 3600

# Connection reuse for XHTTP. Without it every new stream repeats the whole
# TCP + TLS + REALITY handshake; with it the second and later streams ride an
# HTTP connection that is already open and cost no round trips at all. The
# panel copies these to the subscription link, so the client gets them too.
#
# The ranges are picked per connection, which keeps two clients from producing
# the same traffic pattern:
#   maxConcurrency   streams that may share one connection
#   maxConnections   0, so concurrency alone decides when to open another
#   cMaxReuseTimes   sub-connections before the underlying one is retired
#   hMaxRequestTimes requests one HTTP connection carries before it is dropped
#   hKeepAlivePeriod seconds between keepalives, so idle NAT does not cut it
XHTTP_XMUX = {
    "maxConcurrency": "16-32",
    "maxConnections": 0,
    "cMaxReuseTimes": "64-128",
    "hMaxRequestTimes": "600-900",
    "hKeepAlivePeriod": 45,
}

# Above the range a panel is likely to have taken, and clear of the privileged
# ports, so the first suggestion is usually the one that gets kept.
FIRST_PORT = 8443
LAST_PORT = 65535

_dest_cache: Optional[Tuple[float, str]] = None
_dest_lock = threading.Lock()


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


def _connect_seconds(host: str) -> Optional[float]:
    """How long this server takes to open a connection to host:443.

    None when the host did not answer, which is a reason to pass it over
    rather than an error: the other candidates are still there.
    """
    started = time.monotonic()
    try:
        socket.create_connection((host, 443), DEST_PROBE_TIMEOUT).close()
    except OSError:
        return None
    return time.monotonic() - started


def choose_dest() -> str:
    """The candidate this server reaches fastest, measured once an hour.

    XRAY_REALITY_DEST overrides the measurement, which is how a dest on the
    machine itself — a local site on 443 — gets used: nothing beats a round
    trip that never leaves the host.

    The candidates are probed at the same time, so the whole thing costs one
    timeout at worst. If none of them answers, the long-standing default is
    kept: an unreachable dest would be a broken inbound, a slow one is only a
    slow inbound.
    """
    global _dest_cache

    if XRAY_REALITY_DEST:
        return XRAY_REALITY_DEST

    with _dest_lock:
        if _dest_cache and time.monotonic() - _dest_cache[0] < DEST_CACHE_SECONDS:
            return _dest_cache[1]

    with ThreadPoolExecutor(max_workers=len(REALITY_DEST_CANDIDATES)) as pool:
        costs = pool.map(_connect_seconds, REALITY_DEST_CANDIDATES)

    reached = [
        (cost, host)
        for host, cost in zip(REALITY_DEST_CANDIDATES, costs)
        if cost is not None
    ]
    dest = min(reached)[1] if reached else REALITY_DEST

    with _dest_lock:
        _dest_cache = (time.monotonic(), dest)
    return dest


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
        if certificate.certificate_path and certificate.private_key_path:
            # A lineage is named after its first domain, which is the one to
            # fall back on when certbot's output did not list them.
            domain = certificate.domains[0] if certificate.domains else certificate.name
            return domain, certificate.certificate_path, certificate.private_key_path

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

    if transport == "xhttp":
        return {
            "network": "xhttp",
            "security": security,
            "xhttpSettings": {
                "path": f"/{secrets.token_hex(4)}",
                # One request carries both directions, so a stream is up after
                # the first one; the alternatives spend a further round trip
                # opening a second request to download through. This is what
                # `auto` already picks for a REALITY client, written down so
                # that a TLS one gets it too. The cost is that a CDN in front
                # of this would break — the template points clients straight
                # at this server's own port, so there is none.
                "mode": "stream-one",
                # Only host, path and mode belong at this level. The core
                # reads the rest out of `extra`, and so does a client from the
                # link the panel writes.
                "extra": {
                    # A megabyte per upload request, so a client sending
                    # anything of size is not cut into requests that each wait
                    # a round trip.
                    "scMaxEachPostBytes": 1000000,
                    # The floor between two upload requests. The default of
                    # 30 ms is a third of a round trip on a decent link and is
                    # spent doing nothing; 10 ms keeps the request rate sane.
                    "scMinPostsIntervalMs": 10,
                    "xPaddingBytes": "100-1000",
                    "xmux": XHTTP_XMUX,
                },
            },
        }

    return {
        "network": "ws",
        "security": security,
        "wsSettings": {"path": f"/{secrets.token_hex(4)}"},
    }


def _security(security: str, dest: str) -> Dict:
    """The security half: either someone else's handshake, or our own."""
    if security == "reality":
        private_key, _ = generate_x25519_keypair()
        return {
            "realitySettings": {
                "show": False,
                "dest": f"{dest}:443",
                "xver": 0,
                "serverNames": [dest],
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
            "REALITY does not work over WebSocket. Use tcp, grpc or xhttp for "
            "REALITY, or TLS for WebSocket."
        )

    stream = _stream(transport, security)
    stream.update(_security(security, choose_dest() if security == "reality" else ""))
    # Fast Open lets a returning client put its first request into the SYN,
    # which is the one round trip a TCP handshake would otherwise cost. The
    # keepalive is there so a connection kept open for reuse survives the
    # idle timers of the NAT it is usually behind.
    stream["sockopt"] = {"tcpFastOpen": True, "tcpKeepAliveIdle": 100}

    return {
        "tag": _unique_tag(f"VLESS {transport.upper()} {security.upper()}", taken_tags or []),
        "listen": "0.0.0.0",
        "port": _free_port(taken_ports or []),
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": stream,
        # routeOnly keeps the sniffed domain for the routing rules but leaves
        # the address being dialled alone. Without it the core throws away the
        # address the client resolved and looks the domain up again itself,
        # once per connection, through a resolver that does not cache.
        "sniffing": {
            "enabled": True,
            "destOverride": ["http", "tls", "quic"],
            "routeOnly": True,
        },
    }
