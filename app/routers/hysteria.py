"""The one endpoint in this panel that no admin token guards.

Hysteria2 asks a backend whether a password it was given belongs to someone,
and it has no credential of the panel's to present when it asks. What stands in
for one is where the request comes from: the daemon runs on this machine and
reaches the panel over the loopback interface, so a request that arrives any
other way is not the daemon and is refused before the password is looked at.

Two things are checked, not one. The peer address has to be loopback, which
stops anything off the machine. And the request must carry no forwarding
headers, which stops the case the first check alone would miss: a reverse proxy
in front of the panel makes every request look local, and one that does not set
`X-Forwarded-For` would hand the whole internet a loopback address.
"""

import ipaddress

from fastapi import APIRouter, Depends, Request

from app import hysteria
from app.db import Session, get_db
from app.hysteria import auth as hysteria_auth
from app.models.admin import Admin
from app.models.hysteria import (HysteriaAuthRequest, HysteriaAuthResponse,
                                 HysteriaStats)
from app.utils import responses
from config import HYSTERIA_PORT

router = APIRouter(tags=["Hysteria"], prefix="/api")

FORWARDING_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")


def _is_local(request: Request) -> bool:
    client = request.client
    if client is None:
        return False

    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False


@router.post("/hysteria/auth", response_model=HysteriaAuthResponse, include_in_schema=False)
def hysteria_auth_callback(
    payload: HysteriaAuthRequest, request: Request, db: Session = Depends(get_db)
):
    """Whether a password admits its bearer, and who the traffic belongs to.

    Always 200: hysteria reads the body, and an HTTP error would tell it the
    backend is broken rather than that the password is wrong. A refusal is
    `{"ok": false}` whether the password was wrong, the user is expired, or the
    request did not come from the daemon at all — the caller learns nothing it
    did not already know.
    """
    if not _is_local(request) or any(header in request.headers for header in FORWARDING_HEADERS):
        return HysteriaAuthResponse(ok=False)

    identity = hysteria_auth.identify(db, payload.auth)
    if identity is None:
        return HysteriaAuthResponse(ok=False)

    return HysteriaAuthResponse(ok=True, id=identity)


def _stats() -> HysteriaStats:
    running = hysteria.is_enabled() and hysteria.core.started
    reason = None

    if hysteria.is_enabled() and not running:
        # Rendering the configuration is what fails when something is missing,
        # so asking for it is how the panel finds out what to say.
        try:
            hysteria.config.render()
        except hysteria.HysteriaConfigError as err:
            reason = str(err)

    return HysteriaStats(
        enabled=hysteria.is_enabled(),
        running=running,
        version=hysteria.core.version if running else None,
        port=HYSTERIA_PORT,
        reason=reason,
    )


@router.get("/hysteria", response_model=HysteriaStats, responses={403: responses._403})
def get_hysteria_stats(admin: Admin = Depends(Admin.check_sudo_admin)):
    """State of the second core: whether it is on, up, and why not."""
    return _stats()


@router.post("/hysteria/restart", response_model=HysteriaStats, responses={403: responses._403})
def restart_hysteria(admin: Admin = Depends(Admin.check_sudo_admin)):
    """Restart the daemon, picking up whatever the configuration now says.

    A failure is reported in the body rather than raised: the panel wants to
    show why it is down, and every caller here already reads that field.
    """
    if not hysteria.is_enabled():
        return _stats()

    try:
        hysteria.core.restart()
    except Exception as err:
        return HysteriaStats(
            enabled=True, running=hysteria.core.started, port=HYSTERIA_PORT, reason=str(err)
        )

    return _stats()
