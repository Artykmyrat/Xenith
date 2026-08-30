"""Recording what users consumed.

The counters this job reads are destructive: xray's stats are fetched with
`reset=True`, so whatever is read and then not written down is gone. That is
what these tests are about — that a poll ends up on the right user, the right
admin and the right hourly row — rather than about the SQL that gets it there.
"""

from datetime import datetime

import pytest

from app import xray
from app.db import crud
from app.db.models import Admin, NodeUsage, NodeUserUsage, System, User
from app.jobs import record_usages
from app.models.admin import AdminCreate
from app.models.user import UserCreate
from xray_api import exc as xray_exc


class Stat:
    """One line of xray's statistics, as the API hands it over."""

    def __init__(self, name, value, link=None):
        self.name = name
        self.value = value
        self.link = link


class FakeAPI:
    def __init__(self, users=(), outbounds=()):
        self._users = list(users)
        self._outbounds = list(outbounds)
        self.reset_asked = False

    def get_users_stats(self, reset=False, timeout=None):
        self.reset_asked = reset
        return self._users

    def get_outbounds_stats(self, reset=False, timeout=None):
        self.reset_asked = reset
        return self._outbounds


@pytest.fixture(autouse=True)
def alone(monkeypatch):
    """No nodes and no hysteria: the main core is the only source of traffic."""
    monkeypatch.setattr(xray, "nodes", {})
    monkeypatch.setattr(record_usages, "DISABLE_RECORDING_NODE_USAGE", False)


@pytest.fixture
def admin(db):
    return crud.create_admin(db, AdminCreate(username="owner", password="ownerpw", is_sudo=True))


@pytest.fixture
def user(db, admin):
    return crud.create_user(
        db, UserCreate(username="alice", proxies={"vmess": {}}, inbounds={}), admin=admin
    )


def serve(monkeypatch, *stats):
    monkeypatch.setattr(xray, "api", FakeAPI(users=stats))


def run(job, db):
    """Run a job and let this session see what it wrote.

    The job opens its own session through GetDB, so rows this one has already
    loaded would otherwise still read as they were before.
    """
    job()
    db.expire_all()


class TestReadingStats:
    def test_traffic_is_summed_per_user(self):
        api = FakeAPI(users=[Stat("7.alice", 100), Stat("7.alice", 50), Stat("9.bob", 5)])

        params = record_usages.get_users_stats(api)

        assert sorted(params, key=lambda p: p["uid"]) == [
            {"uid": "7", "value": 150},
            {"uid": "9", "value": 5},
        ]

    def test_the_counters_are_cleared_as_they_are_read(self):
        api = FakeAPI(users=[Stat("7.alice", 100)])

        record_usages.get_users_stats(api)

        assert api.reset_asked is True

    def test_idle_users_are_left_out(self):
        api = FakeAPI(users=[Stat("7.alice", 0)])

        assert record_usages.get_users_stats(api) == []

    def test_a_core_that_will_not_answer_yields_nothing(self):
        class Broken:
            def get_users_stats(self, **kwargs):
                raise xray_exc.XrayError("unreachable")

        assert record_usages.get_users_stats(Broken()) == []


class TestRecordingUserUsage:
    def test_the_traffic_lands_on_the_user(self, db, user, monkeypatch):
        serve(monkeypatch, Stat(f"{user.id}.alice", 4096))

        run(record_usages.record_user_usages, db)

        assert db.query(User).get(user.id).used_traffic == 4096

    def test_the_user_is_marked_as_seen(self, db, user, monkeypatch):
        serve(monkeypatch, Stat(f"{user.id}.alice", 1))

        run(record_usages.record_user_usages, db)

        assert db.query(User).get(user.id).online_at is not None

    def test_the_owning_admin_is_charged_too(self, db, user, admin, monkeypatch):
        serve(monkeypatch, Stat(f"{user.id}.alice", 4096))

        run(record_usages.record_user_usages, db)

        assert db.query(Admin).get(admin.id).users_usage == 4096

    def test_an_hourly_row_is_opened_and_added_to(self, db, user, monkeypatch):
        serve(monkeypatch, Stat(f"{user.id}.alice", 100))
        run(record_usages.record_user_usages, db)
        serve(monkeypatch, Stat(f"{user.id}.alice", 50))
        run(record_usages.record_user_usages, db)

        rows = db.query(NodeUserUsage).filter_by(user_id=user.id, node_id=None).all()
        assert len(rows) == 1
        assert rows[0].used_traffic == 150
        assert rows[0].created_at.minute == 0  # one row per hour

    def test_nothing_is_written_when_nothing_moved(self, db, user, monkeypatch):
        serve(monkeypatch)

        run(record_usages.record_user_usages, db)

        assert db.query(User).get(user.id).used_traffic == 0
        assert db.query(NodeUserUsage).count() == 0

    def test_hourly_rows_can_be_turned_off(self, db, user, monkeypatch):
        monkeypatch.setattr(record_usages, "DISABLE_RECORDING_NODE_USAGE", True)
        serve(monkeypatch, Stat(f"{user.id}.alice", 100))

        run(record_usages.record_user_usages, db)

        # The user's own total is the thing limits are enforced on, so it is
        # kept either way; the per-node history is what the setting drops.
        assert db.query(User).get(user.id).used_traffic == 100
        assert db.query(NodeUserUsage).count() == 0


class TestRecordingNodeUsage:
    @pytest.fixture(autouse=True)
    def system_row(self, db):
        db.add(System(uplink=0, downlink=0))
        db.commit()

    def test_both_directions_reach_the_system_totals(self, db, monkeypatch):
        monkeypatch.setattr(
            xray, "api", FakeAPI(outbounds=[Stat("direct", 700, link="uplink"), Stat("direct", 300, link="downlink")])
        )

        run(record_usages.record_node_usages, db)

        system = db.query(System).first()
        assert (system.uplink, system.downlink) == (700, 300)

    def test_an_idle_core_writes_nothing(self, db, monkeypatch):
        monkeypatch.setattr(xray, "api", FakeAPI(outbounds=[]))

        run(record_usages.record_node_usages, db)

        assert db.query(NodeUsage).count() == 0

    def test_the_hourly_node_row_is_opened(self, db, monkeypatch):
        monkeypatch.setattr(xray, "api", FakeAPI(outbounds=[Stat("direct", 10, link="uplink")]))

        run(record_usages.record_node_usages, db)

        row = db.query(NodeUsage).filter_by(node_id=None).one()
        assert row.uplink == 10
        assert row.created_at == datetime.fromisoformat(
            datetime.utcnow().strftime("%Y-%m-%dT%H:00:00")
        )
