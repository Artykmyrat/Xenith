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
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app import hysteria
from app.db import Session, get_db
from app.hysteria import auth as hysteria_auth
from app.hysteria import settings as hysteria_settings
from app.models.admin import Admin
from app.models.hysteria import (HysteriaAuthRequest, HysteriaAuthResponse,
                                 HysteriaSettingsModify,
                                 HysteriaSettingsResponse, HysteriaStats)
from app.utils import certbot, responses

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


def _why_not_running() -> Optional[str]:
    """Why the daemon is down, when the panel can tell.

    Rendering the configuration is what fails when something is missing, so
    asking for it is how the panel finds out what to say. A missing certificate
    is the usual answer and one the Certificates screen can act on.
    """
    try:
        hysteria.config.render()
    except hysteria.HysteriaConfigError as err:
        return str(err)
    return None


def _stats() -> HysteriaStats:
    live = hysteria_settings.current()
    running = live.enabled and hysteria.core.started

    return HysteriaStats(
        enabled=live.enabled,
        running=running,
        version=hysteria.core.version if running else None,
        port=live.port,
        reason=_why_not_running() if live.enabled and not running else None,
    )


def _certificate_names() -> list:
    """Certificate names to choose a domain from, or nothing at all.

    Best effort: certbot being unavailable is already reported through
    `reason`, and failing the whole settings request over it would leave the
    screen blank at the moment it is most needed.
    """
    if not certbot.is_enabled():
        return []
    try:
        return sorted(c.name for c in certbot.list_certificates() if c.certificate_path)
    except certbot.CertbotError:
        return []


def _settings_response() -> HysteriaSettingsResponse:
    live = hysteria_settings.current()
    running = live.enabled and hysteria.core.started

    # Rendered whether or not the daemon is on: an admin setting hysteria up
    # wants to see the file before turning it on, and the reason it will not
    # render is the same reason it would not start.
    try:
        config = hysteria.config.preview(live)
        reason = None
    except hysteria.HysteriaConfigError as err:
        config = None
        reason = str(err)

    return HysteriaSettingsResponse(
        enabled=live.enabled,
        port=live.port,
        domain=live.domain,
        obfs_password=live.obfs_password,
        up_mbps=live.up_mbps,
        down_mbps=live.down_mbps,
        masquerade_url=live.masquerade_url or "",
        stats_port=live.stats_port,
        extra=live.extra,
        updated_at=live.updated_at,
        running=running,
        version=hysteria.core.version if running else None,
        reason=reason,
        config=config,
        certificates=_certificate_names(),
        reserved_keys=list(hysteria_settings.RESERVED_KEYS),
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
            enabled=True,
            running=hysteria.core.started,
            port=hysteria_settings.current().port,
            reason=str(err),
        )

    return _stats()


@router.get(
    "/hysteria/settings",
    response_model=HysteriaSettingsResponse,
    responses={403: responses._403},
)
def get_hysteria_settings(admin: Admin = Depends(Admin.check_sudo_admin)):
    """How the daemon is configured, what it would be started with, and its state."""
    return _settings_response()


@router.put(
    "/hysteria/settings",
    response_model=HysteriaSettingsResponse,
    responses={400: responses._400, 403: responses._403},
)
def modify_hysteria_settings(
    modified: HysteriaSettingsModify,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Change the settings and bring the daemon into line with them.

    The configuration is rendered when the daemon starts, so a change means a
    restart — done here rather than left as a second button, because settings
    that are stored but not running are the thing an admin is least likely to
    notice. Turning hysteria off stops it; turning it on starts it; changing a
    port while it is running restarts it onto the new one.

    A daemon that will not come back up is reported through `reason` rather
    than raised: the settings were saved, and refusing the request would say
    otherwise.
    """
    changes = modified.model_dump(exclude_unset=True)
    if not changes:
        return _settings_response()

    try:
        live = hysteria_settings.save(db, **changes)
    except hysteria_settings.HysteriaSettingsError as err:
        raise HTTPException(status_code=400, detail=str(err))
    except hysteria_settings.HysteriaSchemaError as err:
        # Not a 400: there is nothing wrong with what was sent, and nothing the
        # admin can change in the form to make it work.
        raise HTTPException(status_code=503, detail=str(err))

    try:
        if not live.enabled:
            hysteria.core.stop()
        elif hysteria.core.started:
            hysteria.core.restart()
        else:
            hysteria.core.start()
    except Exception:
        # Left to _settings_response, which asks the configuration itself why
        # rather than repeating whatever the process happened to raise.
        pass

    return _settings_response()
