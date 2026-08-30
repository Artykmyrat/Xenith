"""Hysteria2 support: a second core the panel supervises on the main server.

Nodes carry xray alone — see the design note in the repository — so everything
here is about one process on this machine.
"""

from app import logger
from app.hysteria import config  # noqa: F401
from app.hysteria.config import HysteriaConfigError  # noqa: F401
from app.hysteria import settings  # noqa: F401
from app.hysteria.core import HysteriaCore
from config import HYSTERIA_EXECUTABLE_PATH

core = HysteriaCore(HYSTERIA_EXECUTABLE_PATH)


def is_enabled() -> bool:
    return settings.current().enabled


def ensure_running() -> None:
    """Bring the daemon into line with the settings. Called on a timer.

    Both directions, because the setting is now something an admin can change
    while the panel is up: a daemon that has been turned off is stopped here,
    not only at shutdown. Without that, turning hysteria off in the panel would
    leave the port open and users connected until the next restart.

    A failure to start is logged and left at that. The usual cause is a
    certificate that has not been issued yet, and repeating the traceback every
    few seconds would bury the log without telling anyone more.
    """
    if core.restarting:
        return

    if not is_enabled():
        if core.started:
            core.stop()
        return

    if core.started:
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

    live = settings.current()

    return {
        "tag": TAG,
        "protocol": "hysteria2",
        "port": live.port,
        "network": "hysteria2",
        "tls": "tls",
        "sni": [live.domain] if live.domain else [],
        "host": [],
        "path": "",
        "header_type": "",
        "is_fallback": False,
    }
