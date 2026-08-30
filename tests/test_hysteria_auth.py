"""The callback hysteria2 makes to ask whether a password admits its bearer.

This is the only endpoint in the panel with no admin token in front of it, so
the tests spend most of their attention on what stands in for one: the request
has to come from the daemon, on this machine, and the two ways of pretending
otherwise both have to fail.
"""

import pytest
from fastapi.testclient import TestClient

from app import app as fastapi_app
from app import hysteria
from app.db import crud, get_db
from app.hysteria import auth as hysteria_auth
from app.hysteria.config import HysteriaConfigError
from app.utils import certbot
from app.models.user import UserCreate, UserStatus


from conftest import auth

PATH = "/api/hysteria/auth"


def hysteria_user(db, username, password, status=UserStatus.active):
    user = crud.create_user(
        db,
        UserCreate(username=username, proxies={"hysteria2": {"password": password}}, inbounds={}),
    )
    if status is not UserStatus.active:
        crud.update_user_status(db, user, status)
    return user


class FromHost:
    """Presents the app with a peer address, which is what this endpoint reads.

    The test client always claims to be "testclient"; the address has to be set
    underneath it, in the scope, because that is where Starlette reads it from.
    """

    def __init__(self, app, host):
        self.app = app
        self.host = host

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, client=(self.host, 45678))
        await self.app(scope, receive, send)


def local_client(db, host="127.0.0.1"):
    """A client the app sees as connecting from `host`."""

    def override_db():
        yield db

    fastapi_app.dependency_overrides[get_db] = override_db
    return TestClient(FromHost(fastapi_app, host))


@pytest.fixture(autouse=True)
def clean_overrides():
    yield
    fastapi_app.dependency_overrides.clear()


class TestIdentify:
    def test_a_live_user_is_identified_by_its_password(self, db):
        user = hysteria_user(db, "alice", "s3cret")

        assert hysteria_auth.identify(db, "s3cret") == f"{user.id}.alice"

    def test_the_identity_is_the_one_traffic_is_recorded_under(self, db):
        # `id.username` is what the xray inbounds use as an email, and what the
        # usage job splits on. Hysteria's counters have to land the same way.
        user = hysteria_user(db, "alice", "s3cret")

        assert hysteria_auth.identify(db, "s3cret").split(".", 1)[0] == str(user.id)

    def test_a_wrong_password_is_nobody(self, db):
        hysteria_user(db, "alice", "s3cret")

        assert hysteria_auth.identify(db, "s3cre") is None

    def test_an_empty_password_is_nobody(self, db):
        hysteria_user(db, "alice", "s3cret")

        assert hysteria_auth.identify(db, "") is None

    @pytest.mark.parametrize("status", [UserStatus.disabled, UserStatus.limited, UserStatus.expired])
    def test_a_user_who_may_not_connect_is_refused(self, db, status):
        hysteria_user(db, "alice", "s3cret", status=status)

        assert hysteria_auth.identify(db, "s3cret") is None

    def test_an_on_hold_user_may_connect(self, db):
        # On-hold is the status of a user whose period starts when they first
        # connect, so refusing them here would mean it never starts.
        user = hysteria_user(db, "alice", "s3cret", status=UserStatus.on_hold)

        assert hysteria_auth.identify(db, "s3cret") == f"{user.id}.alice"

    def test_the_password_of_another_protocol_does_not_admit(self, db):
        crud.create_user(
            db,
            UserCreate(username="bob", proxies={"trojan": {"password": "s3cret"}}, inbounds={}),
        )

        assert hysteria_auth.identify(db, "s3cret") is None

    def test_the_right_user_is_picked_out_of_several(self, db):
        hysteria_user(db, "alice", "alice-pw")
        bob = hysteria_user(db, "bob", "bob-pw")

        assert hysteria_auth.identify(db, "bob-pw") == f"{bob.id}.bob"


class TestEndpoint:
    def test_the_daemon_is_told_who_connected(self, db):
        user = hysteria_user(db, "alice", "s3cret")

        response = local_client(db).post(PATH, json={"auth": "s3cret", "addr": "1.2.3.4:5", "tx": 0})

        assert response.status_code == 200
        assert response.json() == {"ok": True, "id": f"{user.id}.alice"}

    def test_a_wrong_password_is_refused_without_an_error(self, db):
        hysteria_user(db, "alice", "s3cret")

        response = local_client(db).post(PATH, json={"auth": "wrong"})

        # 200 with ok=false, not 4xx: an HTTP error tells hysteria the backend
        # is broken, which is a different thing from a password being wrong.
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert response.json()["id"] is None

    def test_a_request_from_off_the_machine_is_refused(self, db):
        hysteria_user(db, "alice", "s3cret")

        response = local_client(db, host="203.0.113.5").post(PATH, json={"auth": "s3cret"})

        assert response.json()["ok"] is False

    def test_a_forwarded_request_is_refused_even_from_loopback(self, db):
        # A reverse proxy on the same host makes every request look local. The
        # header it adds is what gives that away.
        hysteria_user(db, "alice", "s3cret")

        response = local_client(db).post(
            PATH, json={"auth": "s3cret"}, headers={"X-Forwarded-For": "203.0.113.5"}
        )

        assert response.json()["ok"] is False

    @pytest.mark.parametrize("header", ["X-Real-IP", "Forwarded"])
    def test_every_forwarding_header_counts(self, db, header):
        hysteria_user(db, "alice", "s3cret")

        response = local_client(db).post(PATH, json={"auth": "s3cret"}, headers={header: "203.0.113.5"})

        assert response.json()["ok"] is False

    def test_no_admin_token_is_needed(self, db):
        hysteria_user(db, "alice", "s3cret")

        response = local_client(db).post(PATH, json={"auth": "s3cret"})

        assert response.status_code == 200

    def test_a_body_without_a_password_is_refused_rather_than_rejected(self, db):
        response = local_client(db).post(PATH, json={"addr": "1.2.3.4:5"})

        assert response.status_code == 200
        assert response.json()["ok"] is False

    def test_the_endpoint_stays_out_of_the_public_schema(self, db):
        # It is not part of the API anyone writes against, and documenting an
        # unauthenticated endpoint invites attention it does not need.
        schema = fastapi_app.openapi()

        assert PATH not in schema["paths"]


class TestState:
    """The state the dashboard shows, and the restart it offers."""

    def test_reading_it_needs_a_sudo_admin(self, client, plain_admin):
        assert client.get("/api/hysteria", headers=auth(plain_admin)).status_code == 403

    def test_restarting_needs_a_sudo_admin(self, client, plain_admin):
        assert client.post("/api/hysteria/restart", headers=auth(plain_admin)).status_code == 403

    def test_no_credentials_is_rejected(self, client):
        assert client.get("/api/hysteria").status_code == 401

    def test_a_panel_without_hysteria_says_so(self, client, sudo_admin, hysteria_settings, monkeypatch):
        hysteria_settings(enabled=False)

        body = client.get("/api/hysteria", headers=auth(sudo_admin)).json()

        assert body["enabled"] is False and body["running"] is False

    def test_a_daemon_that_cannot_start_explains_itself(self, client, sudo_admin, hysteria_settings, monkeypatch):
        # The dashboard shows this instead of a bare "stopped", because the
        # answer is almost always a certificate nobody has issued yet.
        hysteria_settings(enabled=True)
        monkeypatch.setattr(certbot, "CERTBOT_ENABLED", False)

        body = client.get("/api/hysteria", headers=auth(sudo_admin)).json()

        assert body["enabled"] is True
        assert body["running"] is False
        assert "CERTBOT_ENABLED" in body["reason"]

    def test_restarting_a_disabled_daemon_does_nothing(self, client, sudo_admin, hysteria_settings, monkeypatch):
        hysteria_settings(enabled=False)
        started = []
        monkeypatch.setattr(hysteria.core, "restart", lambda: started.append(True))

        client.post("/api/hysteria/restart", headers=auth(sudo_admin))

        assert started == []

    def test_a_restart_that_fails_is_reported_in_the_body(self, client, sudo_admin, hysteria_settings, monkeypatch):
        hysteria_settings(enabled=True)

        def no_certificate():
            raise HysteriaConfigError("no certificate")

        monkeypatch.setattr(hysteria.core, "restart", no_certificate)

        response = client.post("/api/hysteria/restart", headers=auth(sudo_admin))

        assert response.status_code == 200
        assert response.json()["reason"] == "no certificate"
