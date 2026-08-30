"""Hysteria2 support: a second core the panel supervises on the main server.

Nodes carry xray alone — see the design note in the repository — so everything
here is about one process on this machine.
"""

from app import logger
from app.hysteria import config  # noqa: F401
from app.hysteria.config import HysteriaConfigError  # noqa: F401
from app.hysteria.core import HysteriaCore
from config import (HYSTERIA_DOMAIN, HYSTERIA_ENABLED,
                    HYSTERIA_EXECUTABLE_PATH, HYSTERIA_PORT)

core = HysteriaCore(HYSTERIA_EXECUTABLE_PATH)


def is_enabled() -> bool:
    return bool(HYSTERIA_ENABLED)


def ensure_running() -> None:
    """Start the daemon if it should be running and is not.

    Called on a timer. A failure is logged and left at that: the usual cause is
    a certificate that has not been issued yet, and repeating the traceback
    every few seconds would bury the log without telling anyone more.
    """
    if not is_enabled() or core.started or core.restarting:
        return

    try:
        core.start()
    except Exception as err:
        logger.warning(f"Hysteria2 is not running: {err}")


# The tag the panel knows this daemon's inbound by. Not a tag in any xray
# configuration — it exists so hysteria can be enabled per user, excluded per
# user, and rendered into a subscription the same way an inbound is.
TAG = "Hysteria2"


def inbound():
    """The daemon as an inbound the panel can offer, or None when it is off.

    Shaped like a resolved xray inbound because everything downstream — the
    user dialog, the subscription pipeline — reads that shape. The fields xray
    fills from stream settings are empty here: hysteria has one transport and
    it is not negotiable.
    """
    if not is_enabled():
        return None

    return {
        "tag": TAG,
        "protocol": "hysteria2",
        "port": HYSTERIA_PORT,
        "network": "hysteria2",
        "tls": "tls",
        "sni": [HYSTERIA_DOMAIN] if HYSTERIA_DOMAIN else [],
        "host": [],
        "path": "",
        "header_type": "",
        "is_fallback": False,
    }
