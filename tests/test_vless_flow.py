"""The flow a VLESS account carries, and where it is allowed to appear.

Vision stops the core encrypting a stream that is already encrypted, which is
most of what a VLESS connection costs in CPU. It rides only on raw TCP under
TLS or REALITY, so the value of handing it to every account rests entirely on
everything that renders an account dropping it where it does not belong -- a
client handed a flow its transport cannot carry does not connect at all.
"""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.models import proxy as proxy_models
from app.models.proxy import ProxySettings, ProxyTypes, VLESSSettings
from app.subscription.singbox import SingBoxConfiguration
from app.subscription.v2ray import V2rayShareLink
from app.utils import inbound_template
from app.xray import XRayConfig
from xray_api.types.account import XTLSFlows

VISION = "xtls-rprx-vision"


def settings_of(transport: str, security: str = "reality") -> dict:
    """What the panel reads back out of a generated inbound."""
    inbound = inbound_template.build(transport, security)
    config = XRayConfig(
        {
            "log": {"loglevel": "warning"},
            "inbounds": [inbound],
            "outbounds": [{"protocol": "freedom", "tag": "DIRECT"}],
        },
        api_port=8080,
    )
    return config.inbounds_by_tag[inbound["tag"]]


def vless_link(inbound: dict, flow: str) -> dict:
    """The query of a share link built for that inbound, as a plain dict."""
    link = V2rayShareLink.vless(
        remark="probe",
        address="example.com",
        port=inbound["port"],
        id="00000000-0000-0000-0000-000000000000",
        net=inbound["network"],
        path=inbound["path"],
        tls=inbound["tls"],
        sni=inbound["sni"][0] if inbound["sni"] else "",
        pbk=inbound.get("pbk", ""),
        sid=inbound["sids"][0] if inbound.get("sids") else "",
        mode=inbound.get("mode", ""),
        flow=flow,
    )
    return {key: value[0] for key, value in parse_qs(urlparse(str(link)).query).items()}


class TestDefault:
    def test_a_new_account_is_given_vision(self):
        assert VLESSSettings().flow == XTLSFlows.VISION

    def test_an_account_that_asks_for_no_flow_keeps_none(self):
        # A value that was validated comes back as the value, not the enum --
        # use_enum_values -- which is exactly why the default is a factory.
        assert VLESSSettings(flow="").flow == ""

    def test_the_default_survives_the_trip_through_the_database(self):
        """use_enum_values only applies to values that were validated."""
        fresh = ProxySettings.from_dict(ProxyTypes.VLESS, {})
        stored = ProxySettings.from_dict(ProxyTypes.VLESS, fresh.dict(no_obj=True))

        assert stored.dict(no_obj=True)["flow"] == VISION

    def test_the_default_can_be_turned_off(self, monkeypatch):
        monkeypatch.setattr(proxy_models, "XRAY_DEFAULT_VLESS_FLOW", "")

        assert proxy_models.default_flow() == XTLSFlows.NONE

    def test_a_setting_that_names_no_flow_at_all_is_not_fatal(self, monkeypatch):
        monkeypatch.setattr(proxy_models, "XRAY_DEFAULT_VLESS_FLOW", "xtls-rprx-direct")

        # Refusing to start the panel over a typo in one optional setting
        # would be the worse failure.
        assert proxy_models.default_flow() == XTLSFlows.NONE

    def test_trojan_is_left_alone(self):
        """Vision is a VLESS matter; trojan's flow is not the same thing."""
        assert ProxySettings.from_dict(ProxyTypes.Trojan, {}).flow == XTLSFlows.NONE


class TestWhereItAppears:
    def test_raw_tcp_under_reality_carries_it(self):
        query = vless_link(settings_of("tcp"), VISION)

        assert query["flow"] == VISION

    def test_xhttp_does_not(self):
        # The transport already frames the stream; the core rejects the pair.
        query = vless_link(settings_of("xhttp"), VISION)

        assert "flow" not in query

    def test_grpc_does_not(self):
        query = vless_link(settings_of("grpc"), VISION)

        assert "flow" not in query

    def test_an_account_without_it_still_gets_a_link(self):
        query = vless_link(settings_of("tcp"), "")

        assert "flow" not in query


class TestSingBox:
    def outbound(self, **kwargs):
        kwargs.setdefault("type", "vless")
        kwargs.setdefault("remark", "probe")
        kwargs.setdefault("address", "example.com")
        kwargs.setdefault("port", 443)
        kwargs.setdefault("flow", VISION)
        return SingBoxConfiguration().make_outbound(**kwargs)

    def test_raw_tcp_under_reality_carries_it(self):
        assert self.outbound(net="tcp", tls="reality")["flow"] == VISION

    def test_a_plain_tcp_inbound_does_not(self):
        # `tls or tls != 'none'` used to pass this on the first half, and
        # sing-box refuses an outbound with a flow and no TLS to carry it.
        assert "flow" not in self.outbound(net="tcp", tls="none")

    def test_an_inbound_with_no_security_at_all_does_not(self):
        assert "flow" not in self.outbound(net="tcp", tls="")

    def test_websocket_does_not(self):
        assert "flow" not in self.outbound(net="ws", tls="tls")

    def test_http_headers_over_tcp_do_not(self):
        assert "flow" not in self.outbound(net="tcp", tls="tls", headers="http")
