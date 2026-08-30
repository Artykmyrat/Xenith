"""Answering the daemon's question: does this password belong to a live user?

Hysteria2 authenticates with one string and asks a backend about it, which is
why the panel answers rather than handing the daemon a list. A list would mean
restarting the daemon — dropping every connection on it — each time a user is
added, suspended or revoked. This way nothing restarts and the answer is always
the current one.

The identity handed back is the same `id.username` the xray inbounds use as an
email, so the traffic the daemon reports lands under the same user by the same
parsing.
"""

import secrets
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Proxy, User
from app.models.proxy import ProxyTypes
from app.models.user import UserStatus

# The statuses that may connect, matching what the xray configuration is built
# with: anything else is a user whose access has ended or not begun.
LIVE_STATUSES = (UserStatus.active, UserStatus.on_hold)


def identify(db: Session, password: str) -> Optional[str]:
    """The id to count this connection under, or None to refuse it.

    Every candidate is compared in constant time. The set is small — one row
    per user who has hysteria2 at all — and it is read per connection rather
    than per packet, so the query is cheaper than the cache that would have to
    be invalidated on every user change.
    """
    if not password:
        return None

    rows = (
        db.query(User.id, User.username, Proxy.settings)
        .join(Proxy, Proxy.user_id == User.id)
        .filter(Proxy.type == ProxyTypes.Hysteria2, User.status.in_(LIVE_STATUSES))
        .all()
    )

    match = None
    for user_id, username, settings in rows:
        stored = (settings or {}).get("password")
        # Not breaking on the first hit: the loop takes the same time whether
        # the password is the first candidate, the last, or none of them.
        if stored and secrets.compare_digest(str(stored), password):
            match = f"{user_id}.{username}"

    return match
