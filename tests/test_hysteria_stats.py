"""Hysteria2's traffic, read from the daemon and recorded against its users.

Reading clears the counters, so the tests care about two things above all: that
a malformed answer costs nobody their traffic, and that what is read lands on
the same user the xray statistics would have landed on.
"""

import pytest
import requests

from app import hysteria, xray
from app.db import crud
from app.db.models import Admin, System, User
from app.hysteria import stats as hysteria_stats
from app.jobs import record_usages
from app.models.admin import AdminCreate
from app.models.user import UserCreate

from test_record_usages import FakeAPI, run


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def answers(monkeypatch, payload, status=200):
    """Stand in for the daemon's traffic API, capturing how it was asked."""
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        if isinstance(payload, requests.RequestException):
            raise payload
        return FakeResponse(payload, status)

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


class TestReadingTheDaemon:
    def test_traffic_is_reported_per_user(self, monkeypatch):
        answers(monkeypatch, {"7.alice": {"tx": 100, "rx": 200}, "9.bob": {"tx": 1, "rx": 0}})

        usage = hysteria_stats.collect("secret")

        assert usage.users == [{"uid": "7", "value": 300}, {"uid": "9", "value": 1}]
        assert (usage.up, usage.down) == (101, 200)

    def test_the_counters_are_cleared_as_they_are_read(self, monkeypatch):
        calls = answers(monkeypatch, {})

        hysteria_stats.collect("secret")

        assert "clear=1" in calls[0]["url"]

    def test_it_asks_over_loopback_with_the_secret(self, monkeypatch):
        calls = answers(monkeypatch, {})

        hysteria_stats.collect("s3cr3t")

        assert calls[0]["url"].startswith("http://127.0.0.1:")
        assert calls[0]["headers"]["Authorization"] == "s3cr3t"
        assert calls[0]["timeout"] == hysteria_stats.TIMEOUT

    def test_idle_users_are_left_out(self, monkeypatch):
        answers(monkeypatch, {"7.alice": {"tx": 0, "rx": 0}})

        assert hysteria_stats.collect("secret").users == []

    def test_an_identity_the_panel_did_not_issue_is_ignored(self, monkeypatch):
        answers(monkeypatch, {"someone": {"tx": 5, "rx": 5}, "7.alice": {"tx": 1, "rx": 1}})

        usage = hysteria_stats.collect("secret")

        assert usage.users == [{"uid": "7", "value": 2}]
        assert (usage.up, usage.down) == (1, 1)

    @pytest.mark.parametrize(
        "payload",
        [
            {"7.alice": {"tx": "lots", "rx": 1}},
            {"7.alice": {"tx": -5, "rx": 1}},
            {"7.alice": "nonsense"},
            ["not", "a", "map"],
        ],
    )
    def test_a_misshapen_answer_yields_nothing(self, monkeypatch, payload):
        answers(monkeypatch, payload)

        assert hysteria_stats.collect("secret").users == []

    def test_a_daemon_that_will_not_answer_costs_nobody_anything(self, monkeypatch):
        answers(monkeypatch, requests.ConnectionError("refused"))

        usage = hysteria_stats.collect("secret")

        assert (usage.users, usage.up, usage.down) == ([], 0, 0)

    def test_a_refused_secret_yields_nothing(self, monkeypatch):
        answers(monkeypatch, {}, status=401)

        assert hysteria_stats.collect("secret").users == []


class TestRecordingIt:
    @pytest.fixture
    def user(self, db):
        admin = crud.create_admin(db, AdminCreate(username="owner", password="ownerpw", is_sudo=True))
        return crud.create_user(
            db,
            UserCreate(username="alice", proxies={"hysteria2": {"password": "pw"}}, inbounds={}),
            admin=admin,
        )

    @pytest.fixture(autouse=True)
    def running(self, monkeypatch, hysteria_settings, db):
        """A hysteria that is on and running, and an xray with nothing to say."""
        monkeypatch.setattr(xray, "nodes", {})
        monkeypatch.setattr(xray, "api", FakeAPI())
        hysteria_settings(enabled=True)
        monkeypatch.setattr(type(hysteria.core), "started", property(lambda self: True))
        db.add(System(uplink=0, downlink=0))
        db.commit()

    def test_the_traffic_lands_on_the_user(self, db, user, monkeypatch):
        answers(monkeypatch, {f"{user.id}.alice": {"tx": 300, "rx": 700}})

        run(record_usages.record_user_usages, db)

        assert db.query(User).get(user.id).used_traffic == 1000

    def test_the_owning_admin_is_charged_too(self, db, user, monkeypatch):
        answers(monkeypatch, {f"{user.id}.alice": {"tx": 300, "rx": 700}})

        run(record_usages.record_user_usages, db)

        assert db.query(Admin).get(user.admin_id).users_usage == 1000

    def test_it_reaches_the_system_totals(self, db, user, monkeypatch):
        # xray's share of these comes from its outbound statistics, which
        # hysteria has none of; without this its traffic would be invisible
        # on the dashboard.
        answers(monkeypatch, {f"{user.id}.alice": {"tx": 300, "rx": 700}})

        run(record_usages.record_user_usages, db)

        system = db.query(System).first()
        assert (system.uplink, system.downlink) == (300, 700)

    def test_it_adds_to_what_xray_reported_rather_than_replacing_it(self, db, user, monkeypatch):
        from test_record_usages import Stat

        monkeypatch.setattr(xray, "api", FakeAPI(users=[Stat(f"{user.id}.alice", 500)]))
        answers(monkeypatch, {f"{user.id}.alice": {"tx": 100, "rx": 400}})

        run(record_usages.record_user_usages, db)

        assert db.query(User).get(user.id).used_traffic == 1000

    def test_the_daemon_is_not_polled_while_the_feature_is_off(self, db, user, hysteria_settings, monkeypatch):
        hysteria_settings(enabled=False)
        calls = answers(monkeypatch, {f"{user.id}.alice": {"tx": 300, "rx": 700}})

        run(record_usages.record_user_usages, db)

        assert calls == []
        assert db.query(User).get(user.id).used_traffic == 0

    def test_a_stopped_daemon_is_not_polled(self, db, user, monkeypatch):
        monkeypatch.setattr(type(hysteria.core), "started", property(lambda self: False))
        calls = answers(monkeypatch, {f"{user.id}.alice": {"tx": 300, "rx": 700}})

        run(record_usages.record_user_usages, db)

        assert calls == []
