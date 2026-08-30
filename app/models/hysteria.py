from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class HysteriaAuthRequest(BaseModel):
    """What the daemon sends for each connection it is asked to accept.

    Its shape is hysteria's, not the panel's: `addr` is the client, `auth` the
    string the client presented, `tx` the transmission rate it asked for. Only
    the password is used — the address is the client's own claim about itself,
    and the panel has no rate to negotiate.
    """

    auth: str = Field(default="", max_length=512)
    addr: Optional[str] = None
    tx: Optional[int] = None


class HysteriaAuthResponse(BaseModel):
    ok: bool
    # The user this connection's traffic belongs to, in the `id.username` form
    # the xray inbounds also use. Absent on a refusal.
    id: Optional[str] = None


class HysteriaStats(BaseModel):
    """What the panel can say about the second core without asking it anything."""

    enabled: bool
    running: bool
    version: Optional[str] = None
    port: int
    # Why it is not running, when the panel can tell: a missing certificate is
    # the usual answer, and it is one the Certificates screen can act on.
    reason: Optional[str] = None


class HysteriaSettingsResponse(BaseModel):
    """Everything the Core screen needs to show and edit the daemon."""

    enabled: bool
    port: int
    domain: Optional[str] = None
    obfs_password: Optional[str] = None
    up_mbps: int
    down_mbps: int
    masquerade_url: str
    stats_port: int
    extra: Optional[Dict] = None
    updated_at: Optional[datetime] = None

    # State, so the screen is one request rather than two.
    running: bool
    version: Optional[str] = None
    # Why the daemon is not running, or why the configuration will not render.
    # A missing certificate is the usual answer and the one an admin can act on.
    reason: Optional[str] = None
    # The file the daemon would be started with, as it would be written. None
    # when it cannot be rendered at all, which `reason` then explains.
    config: Optional[str] = None
    # Certificate names certbot holds, to choose a domain from rather than type one.
    certificates: List[str] = []
    # The keys `extra` may not carry, so the UI can say so before a save fails.
    reserved_keys: List[str] = []


class HysteriaSettingsModify(BaseModel):
    """A change to the settings. Only the fields sent are touched."""

    enabled: Optional[bool] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    domain: Optional[str] = Field(default=None, max_length=253)
    obfs_password: Optional[str] = Field(default=None, max_length=128)
    up_mbps: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    down_mbps: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    masquerade_url: Optional[str] = Field(default=None, max_length=512)
    stats_port: Optional[int] = Field(default=None, ge=1, le=65535)
    extra: Optional[Dict] = None

    @field_validator("domain", "obfs_password", "masquerade_url", mode="before")
    @classmethod
    def blank_is_unset(cls, value):
        """An emptied field means "no value", not the empty string.

        The form sends "" when a box is cleared, and a domain of "" would then
        be stored and later compared against certificate names as if someone
        had asked for a certificate named nothing.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value
