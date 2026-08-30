"""What the hysteria2 daemon is configured with, and where that comes from.

One row in the database, seeded on first upgrade from the .env variables that
used to be the only answer. Those variables are still read — they are the
defaults a fresh installation gets, and the fallback if the row is somehow
missing — but the row is what the panel edits and what everything here reads.

Held in memory between changes. `enabled` is asked on every health check and
on every user the subscription pipeline renders, which is far too often to go
to the database for; a change from the panel drops the cache, so the only way
to see a stale answer is to edit the row by hand behind the panel's back.

Nothing here restarts the daemon. The configuration is rendered when it
starts, so a change means a restart, and that is the caller's decision to make
and to report on.
"""

import re
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Dict, Optional

from config import (HYSTERIA_DOMAIN, HYSTERIA_DOWN_MBPS, HYSTERIA_ENABLED,
                    HYSTERIA_MASQUERADE_URL, HYSTERIA_OBFS_PASSWORD,
                    HYSTERIA_PORT, HYSTERIA_STATS_PORT, HYSTERIA_UP_MBPS,
                    UVICORN_PORT)

# The keys the panel writes itself. An admin may add to the rendered
# configuration through `extra`, but not replace these: `auth` is what ties a
# connection to a user, `trafficStats` is what counts it, and `tls` and
# `listen` are what the panel reports and what the subscription links point
# at. Silently overriding any of them would leave the panel describing a
# daemon that is not the one running.
RESERVED_KEYS = ("listen", "tls", "auth", "trafficStats")

# Hysteria has to be reachable, and 1024 and below needs a privilege the panel
# may not have. It is allowed anyway — 443 is the whole point of the protocol —
# but nothing above the 16-bit range is.
MIN_PORT, MAX_PORT = 1, 65535

DOMAIN_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$")


class HysteriaSettingsError(Exception):
    """The settings could not be accepted; the message is safe to show."""


class HysteriaSchemaError(Exception):
    """The table is not there, so nothing can be stored.

    A database the migrations have not been run against. Its own exception
    because it is not the admin's mistake and there is nothing to correct in
    the form — the answer is `alembic upgrade head`, and saying so beats a
    traceback in the log and a blank failure on the screen.
    """


def _is_missing_table(err: Exception) -> bool:
    """Whether a database error is "that table does not exist".

    Matched on the message because every engine words it differently and none
    of them give it a code worth branching on: SQLite says "no such table",
    MySQL "doesn't exist", PostgreSQL "does not exist".
    """
    message = str(getattr(err, "orig", err)).lower()
    return "hysteria_settings" in message and (
        "no such table" in message or "doesn't exist" in message or "does not exist" in message
    )


@dataclass(frozen=True)
class Settings:
    enabled: bool
    port: int
    domain: Optional[str]
    obfs_password: Optional[str]
    up_mbps: int
    down_mbps: int
    # Empty means no masquerade block at all: the port then answers an
    # unauthenticated visitor with an error instead of a web page. A worse
    # disguise, but a legitimate choice, and not a reason to refuse a save.
    masquerade_url: Optional[str]
    stats_port: int
    extra: Optional[Dict]
    updated_at: Optional[datetime] = None


def _from_env() -> Settings:
    """What .env asks for: the defaults, and the fallback if the row is gone."""
    return Settings(
        enabled=bool(HYSTERIA_ENABLED),
        port=int(HYSTERIA_PORT),
        domain=HYSTERIA_DOMAIN or None,
        obfs_password=HYSTERIA_OBFS_PASSWORD or None,
        up_mbps=int(HYSTERIA_UP_MBPS),
        down_mbps=int(HYSTERIA_DOWN_MBPS),
        masquerade_url=HYSTERIA_MASQUERADE_URL or None,
        stats_port=int(HYSTERIA_STATS_PORT),
        extra=None,
    )


def _from_row(row) -> Settings:
    return Settings(
        enabled=bool(row.enabled),
        port=int(row.port),
        domain=row.domain or None,
        obfs_password=row.obfs_password or None,
        up_mbps=int(row.up_mbps or 0),
        down_mbps=int(row.down_mbps or 0),
        masquerade_url=row.masquerade_url or None,
        stats_port=int(row.stats_port),
        extra=row.extra or None,
        updated_at=row.updated_at,
    )


_cached: Optional[Settings] = None
_lock = threading.Lock()
_warned_about_fallback = False


def _warn_about_fallback_once(err: Exception) -> None:
    global _warned_about_fallback

    if _warned_about_fallback:
        return

    _warned_about_fallback = True

    from app import logger

    logger.warning(
        f"Could not read the hysteria settings ({err}); falling back to what .env says. "
        "If this is not a database that is still being migrated, run `alembic upgrade head`."
    )


def invalidate() -> None:
    global _cached
    with _lock:
        _cached = None


def _load() -> Settings:
    from app.db import GetDB
    from app.db.models import HysteriaSettings

    with GetDB() as db:
        row = db.query(HysteriaSettings).first()
        return _from_row(row) if row else _from_env()


def current() -> Settings:
    """The settings in force, read once and remembered."""
    global _cached

    if _cached is not None:
        return _cached

    with _lock:
        if _cached is None:
            try:
                _cached = _load()
            except Exception as err:
                # A database that is not there yet — mid-migration, or a test
                # that has not built its schema — must not stop the panel from
                # starting. .env is the honest fallback, said out loud once so
                # an unmigrated database does not pass for a configured one.
                _warn_about_fallback_once(err)
                return _from_env()
    return _cached


def _validated(settings: Settings) -> Settings:
    if not MIN_PORT <= settings.port <= MAX_PORT:
        raise HysteriaSettingsError(f"{settings.port} is not a valid port.")
    if not MIN_PORT <= settings.stats_port <= MAX_PORT:
        raise HysteriaSettingsError(f"{settings.stats_port} is not a valid port.")
    if settings.port == settings.stats_port:
        raise HysteriaSettingsError(
            "The traffic API cannot share the daemon's own port."
        )
    if settings.port == UVICORN_PORT or settings.stats_port == UVICORN_PORT:
        raise HysteriaSettingsError(
            f"Port {UVICORN_PORT} is the panel's own. Hysteria would not start, and if it "
            "did the panel would stop answering."
        )
    if settings.domain and not DOMAIN_RE.match(settings.domain):
        raise HysteriaSettingsError(f"{settings.domain!r} is not a valid domain name.")
    if settings.up_mbps < 0 or settings.down_mbps < 0:
        raise HysteriaSettingsError("Bandwidth cannot be negative.")
    if settings.masquerade_url and not settings.masquerade_url.startswith(("http://", "https://")):
        raise HysteriaSettingsError("The masquerade URL has to be http:// or https://.")

    if settings.extra is not None:
        if not isinstance(settings.extra, dict):
            raise HysteriaSettingsError("Extra configuration has to be a mapping of keys to values.")
        clashes = [key for key in RESERVED_KEYS if key in settings.extra]
        if clashes:
            raise HysteriaSettingsError(
                f"{', '.join(clashes)} {'is' if len(clashes) == 1 else 'are'} written by the panel "
                "and cannot be set here. Everything else hysteria understands can."
            )

    return settings


def save(db, **changes) -> Settings:
    """Store a change, returning the settings now in force.

    Only the fields named are touched, so the dashboard can send one of them
    without having to send the rest back unaltered.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from app.db.models import HysteriaSettings

    try:
        row = db.query(HysteriaSettings).first()
    except SQLAlchemyError as err:
        db.rollback()
        if _is_missing_table(err):
            raise HysteriaSchemaError(
                "The hysteria settings table is missing, so nothing can be saved. Run "
                "`alembic upgrade head` against this panel's database."
            )
        raise

    if row is None:
        # A panel whose row never got seeded — a database created from the
        # models rather than through the migration. Start from .env, which is
        # what the migration would have done.
        base = _from_env()
        row = HysteriaSettings(
            id=1,
            enabled=base.enabled,
            port=base.port,
            domain=base.domain,
            obfs_password=base.obfs_password,
            up_mbps=base.up_mbps,
            down_mbps=base.down_mbps,
            masquerade_url=base.masquerade_url,
            stats_port=base.stats_port,
            extra=base.extra,
        )
        db.add(row)

    wanted = _validated(replace(_from_row(row), **changes))

    row.enabled = wanted.enabled
    row.port = wanted.port
    row.domain = wanted.domain
    row.obfs_password = wanted.obfs_password
    row.up_mbps = wanted.up_mbps
    row.down_mbps = wanted.down_mbps
    row.masquerade_url = wanted.masquerade_url or ""
    row.stats_port = wanted.stats_port
    row.extra = wanted.extra
    row.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(row)

    invalidate()
    return current()
