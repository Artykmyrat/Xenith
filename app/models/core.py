from typing import List, Literal

from pydantic import BaseModel, Field


class CoreStats(BaseModel):
    version: str
    started: bool
    logs_websocket: str


class InboundTemplateRequest(BaseModel):
    """What the configuration screen already has, so the template avoids it.

    The tags and ports come from the editor rather than the saved file: two
    templates added one after another would otherwise collide, since neither
    has been saved yet when the second is asked for.
    """

    transport: Literal["tcp", "grpc", "ws", "xhttp"]
    security: Literal["tls", "reality"]
    taken_tags: List[str] = Field(default_factory=list, max_length=500)
    taken_ports: List[int] = Field(default_factory=list, max_length=500)
