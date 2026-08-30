from typing import Optional

from pydantic import BaseModel, Field


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
