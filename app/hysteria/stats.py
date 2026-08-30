"""What the daemon says its users consumed since the last time it was asked.

Hysteria2 keeps per-user counters and hands them over on request. The request
clears them, which is the same bargain xray's `reset=True` makes and the same
risk: traffic that is read and then not written down is gone. That is why this
is read from one place only — the usage job — and why nothing else may poll it.

The keys are the identities the auth callback handed out, `id.username`, so
what comes back here needs no translation to line up with the xray statistics
it is merged into.
"""

from dataclasses import dataclass, field
from typing import Dict, List

import requests

from app import logger
from config import HYSTERIA_STATS_PORT

# Short: the job that calls this runs on a timer, and a daemon that is not
# answering is a daemon whose traffic is not moving either.
TIMEOUT = 5


@dataclass
class Usage:
    """Per-user traffic, and the totals it adds up to."""

    users: List[Dict] = field(default_factory=list)
    up: int = 0
    down: int = 0


def _url() -> str:
    return f"http://127.0.0.1:{HYSTERIA_STATS_PORT}/traffic?clear=1"


def collect(secret: str) -> Usage:
    """Read and clear the daemon's counters.

    Anything unreachable, unparseable or misshapen yields nothing rather than
    raising: this runs inside the job that records xray's traffic too, and a
    hysteria that is down must not cost anyone else their usage.
    """
    try:
        response = requests.get(_url(), headers={"Authorization": secret}, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as err:
        logger.debug(f"Hysteria2 traffic could not be read: {err}")
        return Usage()

    if not isinstance(payload, dict):
        return Usage()

    usage = Usage()
    for identity, counters in payload.items():
        if not isinstance(counters, dict):
            continue

        tx = counters.get("tx") or 0
        rx = counters.get("rx") or 0
        if not isinstance(tx, int) or not isinstance(rx, int) or tx < 0 or rx < 0:
            continue
        if not (tx or rx):
            continue

        uid = str(identity).split(".", 1)[0]
        if not uid.isdigit():
            # An identity the panel did not issue. Nothing to charge it to.
            continue

        usage.users.append({"uid": uid, "value": tx + rx})
        usage.up += tx
        usage.down += rx

    return usage
