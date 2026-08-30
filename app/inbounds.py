"""Every inbound the panel offers, whichever daemon serves it.

The panel used to equate "inbound" with "an entry in the xray configuration",
which was true while xray was the only thing running. Hysteria2 is not in that
file and never will be, so the places that ask *what can a user be given* ask
here instead, and the places that talk to the xray API keep asking xray.
"""

from typing import Dict, List, Optional

from app import hysteria, xray


def _extra() -> List[dict]:
    """Inbounds contributed by daemons other than the core."""
    inbound = hysteria.inbound()
    return [inbound] if inbound else []


def by_protocol() -> Dict[str, List[dict]]:
    registry = {protocol: list(inbounds) for protocol, inbounds in xray.config.inbounds_by_protocol.items()}
    for inbound in _extra():
        registry.setdefault(inbound["protocol"], []).append(inbound)
    return registry


def by_tag() -> Dict[str, dict]:
    registry = dict(xray.config.inbounds_by_tag)
    for inbound in _extra():
        registry[inbound["tag"]] = inbound
    return registry


def get(tag: str) -> Optional[dict]:
    return by_tag().get(tag)
