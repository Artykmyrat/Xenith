"""The link a client imports to reach the hysteria2 daemon.

Deliberately outside `app/subscription`: everything there is built around an
xray inbound and the Hosts screen that decorates it — an address list, a path
template, an ALPN, a fingerprint. Hysteria has one endpoint and one way to
reach it, so a link is a short function rather than a configuration.
"""

from typing import Dict, Optional
from urllib.parse import quote, urlencode

from config import (HYSTERIA_DOMAIN, HYSTERIA_OBFS_PASSWORD, HYSTERIA_PORT,
                    XRAY_SUBSCRIPTION_URL_PREFIX)


def address() -> str:
    """The host clients connect to.

    It has to be a name the certificate covers, since hysteria2 is QUIC with
    real TLS and a client will check it. HYSTERIA_DOMAIN says which; without
    one, the panel's own subscription domain is the best guess available, and
    an empty answer is better than a wrong one — a link nobody can build is
    easier to notice than a link that fails to verify.
    """
    if HYSTERIA_DOMAIN:
        return HYSTERIA_DOMAIN

    prefix = XRAY_SUBSCRIPTION_URL_PREFIX
    if prefix:
        host = prefix.split("://", 1)[-1].split("/", 1)[0]
        return host.rsplit(":", 1)[0] if host.count(":") == 1 else host

    return ""


def link(settings: Dict, remark: str) -> Optional[str]:
    """One `hy2://` link, or None when there is no address to point it at."""
    password = (settings or {}).get("password")
    host = address()
    if not password or not host:
        return None

    query = {"sni": host}
    if HYSTERIA_OBFS_PASSWORD:
        # A client that does not send the same obfuscation password is not
        # rejected — it is not answered at all, so this is not optional.
        query["obfs"] = "salamander"
        query["obfs-password"] = HYSTERIA_OBFS_PASSWORD

    return (
        f"hy2://{quote(str(password), safe='')}@{host}:{HYSTERIA_PORT}"
        f"?{urlencode(query)}#{quote(remark, safe='')}"
    )
