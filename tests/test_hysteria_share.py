"""The link a client imports, and the inbound the panel offers it under.

Hysteria has no row on the Hosts screen and no entry in the xray configuration,
so these tests are mostly about the seam: that the panel offers it like an
inbound, and that a subscription carries it without the xray pipeline having to
know anything about it.
"""

from urllib.parse import parse_qs, unquote, urlparse

import pytest

from app import hysteria, inbounds, xray
from app.hysteria import share as hysteria_share
from app.models.proxy import ProxyTypes
from app.subscription import share as subscription_share


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(hysteria, "HYSTERIA_ENABLED", True)
    monkeypatch.setattr(hysteria_share, "HYSTERIA_DOMAIN", "vpn.example.com")
    monkeypatch.setattr(hysteria_share, "HYSTERIA_OBFS_PASSWORD", "")
    monkeypatch.setattr(hysteria_share, "HYSTERIA_PORT", 443)


class TestTheInbound:
    def test_it_is_offered_only_while_the_daemon_is_configured(self, monkeypatch):
        assert hysteria.inbound()["tag"] == hysteria.TAG

        monkeypatch.setattr(hysteria, "HYSTERIA_ENABLED", False)
        assert hysteria.inbound() is None

    def test_it_joins_the_inbounds_the_panel_offers(self):
        assert [i["tag"] for i in inbounds.by_protocol()["hysteria2"]] == [hysteria.TAG]
        assert inbounds.by_tag()[hysteria.TAG]["protocol"] == "hysteria2"

    def test_the_core_keeps_its_own_registry_to_itself(self):
        # Nothing may push a hysteria account at the xray API, so the tag must
        # not appear where that code looks.
        assert hysteria.TAG not in xray.config.inbounds_by_tag

    def test_the_xray_inbounds_are_still_all_there(self):
        for protocol, xray_inbounds in xray.config.inbounds_by_protocol.items():
            offered = [i["tag"] for i in inbounds.by_protocol()[protocol]]
            assert [i["tag"] for i in xray_inbounds] == offered


class TestTheLink:
    def test_it_carries_the_password_and_the_endpoint(self):
        link = hysteria_share.link({"password": "s3cret"}, "alice")

        parsed = urlparse(link)
        assert parsed.scheme == "hy2"
        assert parsed.username == "s3cret"
        assert (parsed.hostname, parsed.port) == ("vpn.example.com", 443)

    def test_the_name_survives_the_trip(self):
        link = hysteria_share.link({"password": "pw"}, "🚀 Marz (alice)")

        assert unquote(urlparse(link).fragment) == "🚀 Marz (alice)"

    def test_a_password_with_awkward_characters_is_encoded(self):
        link = hysteria_share.link({"password": "p@ss word/#1"}, "alice")

        assert urlparse(link).username == "p%40ss%20word%2F%231"
        assert urlparse(link).hostname == "vpn.example.com"

    def test_the_certificate_name_is_sent_as_sni(self):
        link = hysteria_share.link({"password": "pw"}, "alice")

        assert parse_qs(urlparse(link).query)["sni"] == ["vpn.example.com"]

    def test_obfuscation_is_absent_until_it_is_configured(self):
        query = parse_qs(urlparse(hysteria_share.link({"password": "pw"}, "a")).query)

        assert "obfs" not in query

    def test_obfuscation_reaches_the_client(self, monkeypatch):
        # A client without the password is not refused, it is ignored, so the
        # link is the only way it ever learns this.
        monkeypatch.setattr(hysteria_share, "HYSTERIA_OBFS_PASSWORD", "salt")

        query = parse_qs(urlparse(hysteria_share.link({"password": "pw"}, "a")).query)

        assert query["obfs"] == ["salamander"] and query["obfs-password"] == ["salt"]

    def test_no_address_means_no_link(self, monkeypatch):
        monkeypatch.setattr(hysteria_share, "HYSTERIA_DOMAIN", "")
        monkeypatch.setattr(hysteria_share, "XRAY_SUBSCRIPTION_URL_PREFIX", "")

        assert hysteria_share.link({"password": "pw"}, "alice") is None

    def test_the_panel_domain_stands_in_for_an_unset_one(self, monkeypatch):
        monkeypatch.setattr(hysteria_share, "HYSTERIA_DOMAIN", "")
        monkeypatch.setattr(hysteria_share, "XRAY_SUBSCRIPTION_URL_PREFIX", "https://panel.example.com:2053")

        assert urlparse(hysteria_share.link({"password": "pw"}, "a")).hostname == "panel.example.com"

    def test_a_user_without_a_password_gets_nothing(self):
        assert hysteria_share.link({}, "alice") is None


class TestInTheSubscription:
    def enabled_for(self, tags):
        return (
            {ProxyTypes.Hysteria2: {"password": "pw"}},
            {ProxyTypes.Hysteria2: tags},
            {"USERNAME": "alice"},
        )

    def test_the_link_is_appended_to_the_v2ray_list(self):
        proxies, user_inbounds, variables = self.enabled_for([hysteria.TAG])

        links = subscription_share.generate_hysteria_links(proxies, user_inbounds, variables)

        assert len(links) == 1 and links[0].startswith("hy2://")

    def test_the_name_follows_the_username(self):
        proxies, user_inbounds, variables = self.enabled_for([hysteria.TAG])

        link = subscription_share.generate_hysteria_links(proxies, user_inbounds, variables)[0]

        assert "alice" in unquote(urlparse(link).fragment)

    def test_a_user_who_was_not_given_the_inbound_gets_no_link(self):
        proxies, user_inbounds, variables = self.enabled_for([])

        assert subscription_share.generate_hysteria_links(proxies, user_inbounds, variables) == []

    def test_a_user_without_the_protocol_gets_no_link(self):
        links = subscription_share.generate_hysteria_links(
            {ProxyTypes.VMess: {"id": "x"}}, {ProxyTypes.Hysteria2: [hysteria.TAG]}, {"USERNAME": "a"}
        )

        assert links == []
