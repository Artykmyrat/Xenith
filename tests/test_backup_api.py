"""The /api/backups endpoints.

The screen is sudo only — a restore replaces the database the panel is running
on — so authorisation is checked on every route. Beyond that the tests care
about the round trip a migration is made of: upload an archive from another
install, read what it holds, restore the parts of it that apply here.
"""

import io
import sqlite3
import tarfile

import pytest

from conftest import auth
from test_backup import marzban_archive


@pytest.fixture
def archive(client, sudo_admin, backup_host):
    """One backup of this install, made through the API."""
    response = client.post("/api/backups", json={}, headers=auth(sudo_admin))
    assert response.status_code == 200
    return response.json()["backups"][0]


class TestAuthorisation:
    ENDPOINTS = [
        ("get", "/api/backups"),
        ("post", "/api/backups"),
        ("get", "/api/backups/xenith-20240101-000000.tar.gz"),
        ("get", "/api/backups/xenith-20240101-000000.tar.gz/download"),
        ("post", "/api/backups/xenith-20240101-000000.tar.gz/restore"),
        ("delete", "/api/backups/xenith-20240101-000000.tar.gz"),
    ]

    @staticmethod
    def call(client, method, path, **kwargs):
        if method == "post":
            kwargs["json"] = {"items": ["database"]}
        return getattr(client, method)(path, **kwargs)

    @pytest.mark.parametrize("method, path", ENDPOINTS)
    def test_no_credentials_is_rejected(self, client, method, path):
        assert self.call(client, method, path).status_code == 401

    @pytest.mark.parametrize("method, path", ENDPOINTS)
    def test_a_reseller_is_rejected(self, client, plain_admin, method, path):
        assert self.call(client, method, path, headers=auth(plain_admin)).status_code == 403

    def test_an_upload_is_sudo_only(self, client, plain_admin):
        response = client.post(
            "/api/backups/upload",
            files={"file": ("backup.tar.gz", io.BytesIO(b"x"), "application/gzip")},
            headers=auth(plain_admin),
        )
        assert response.status_code == 403


class TestStatus:
    def test_the_screen_is_told_where_everything_lives(self, client, sudo_admin, backup_host):
        body = client.get("/api/backups", headers=auth(sudo_admin)).json()

        assert body["enabled"] is True and body["writable"] is True
        assert set(body["paths"]) == {"backups", "data", "env", "xray_config"}
        assert body["database"]["kind"] == "sqlite"
        assert body["backups"] == [] and body["total_bytes"] == 0

    def test_the_database_password_is_never_sent(self, client, sudo_admin, backup_host, monkeypatch):
        from app.utils import backup

        monkeypatch.setattr(
            backup, "SQLALCHEMY_DATABASE_URL", "mysql+pymysql://root:secret@127.0.0.1/xenith"
        )
        body = client.get("/api/backups", headers=auth(sudo_admin)).json()

        assert body["database"]["target"] == "xenith"
        assert "secret" not in str(body)

    def test_a_directory_that_cannot_be_written_is_reported_not_raised(
        self, client, sudo_admin, backup_host, monkeypatch
    ):
        from app.utils import backup

        monkeypatch.setattr(backup, "BACKUP_ENABLED", False)
        body = client.get("/api/backups", headers=auth(sudo_admin)).json()

        assert body["writable"] is False
        assert body["reason"]


class TestMaking:
    def test_a_backup_appears_in_the_same_response(self, client, sudo_admin, backup_host):
        response = client.post(
            "/api/backups", json={"note": "before the upgrade"}, headers=auth(sudo_admin)
        )

        body = response.json()
        assert response.status_code == 200
        assert len(body["backups"]) == 1
        assert body["backups"][0]["kind"] == "manual"
        assert body["backups"][0]["note"] == "before the upgrade"
        assert body["total_bytes"] == body["backups"][0]["size"]

    def test_it_can_be_downloaded_back(self, client, sudo_admin, archive):
        response = client.get(f"/api/backups/{archive['name']}/download", headers=auth(sudo_admin))

        assert response.status_code == 200
        assert archive["name"] in response.headers["content-disposition"]
        with tarfile.open(fileobj=io.BytesIO(response.content)) as tar:
            assert "xenith-backup.json" in tar.getnames()

    def test_deleting_one_leaves_the_rest(self, client, sudo_admin, archive):
        client.post("/api/backups", json={}, headers=auth(sudo_admin))

        body = client.delete(f"/api/backups/{archive['name']}", headers=auth(sudo_admin)).json()

        assert [item["name"] for item in body["backups"]] != [archive["name"]]
        assert len(body["backups"]) == 1

    def test_a_name_that_is_not_one_never_reaches_the_disk(self, client, sudo_admin, backup_host):
        for name in ("..%2F..%2Fetc%2Fpasswd", "notes.txt", "missing.tar.gz"):
            response = client.delete(f"/api/backups/{name}", headers=auth(sudo_admin))
            assert response.status_code in (400, 404), name


class TestImporting:
    def upload(self, client, admin, name, content):
        return client.post(
            "/api/backups/upload",
            files={"file": (name, io.BytesIO(content), "application/gzip")},
            headers=auth(admin),
        )

    def test_a_marzban_archive_is_read_before_anything_is_applied(
        self, client, sudo_admin, backup_host
    ):
        path = marzban_archive(backup_host["root"] / "marzban.tar.gz", backup_host)

        body = self.upload(client, sudo_admin, "backup-2024.tar.gz", path.read_bytes()).json()

        assert body["source"] == "marzban"
        assert body["restorable"] == ["database", "env", "xray_config", "data"]
        # Stored, not applied: the database still holds what it did before.
        connection = sqlite3.connect(str(backup_host["database"]))
        assert connection.execute("SELECT name FROM users").fetchone()[0] == "original"
        connection.close()

    def test_a_file_that_is_not_a_backup_is_refused(self, client, sudo_admin, backup_host):
        response = self.upload(client, sudo_admin, "holiday.jpg", b"\xff\xd8\xff")

        assert response.status_code == 400
        assert client.get("/api/backups", headers=auth(sudo_admin)).json()["backups"] == []


class TestRestoring:
    def change(self, path, value):
        connection = sqlite3.connect(str(path))
        connection.execute("UPDATE users SET name = ?", (value,))
        connection.commit()
        connection.close()

    def read(self, path):
        connection = sqlite3.connect(str(path))
        try:
            return connection.execute("SELECT name FROM users").fetchone()[0]
        finally:
            connection.close()

    def test_the_database_comes_back_and_the_panel_is_told_to_restart(
        self, client, sudo_admin, backup_host, archive
    ):
        self.change(backup_host["database"], "changed")

        response = client.post(
            f"/api/backups/{archive['name']}/restore",
            json={"items": ["database"]},
            headers=auth(sudo_admin),
        )

        body = response.json()
        assert response.status_code == 200
        assert body["applied"] == ["database"]
        assert body["restart_required"] is True
        assert body["safety_backup"].startswith("pre-restore-")
        assert "Restart" in body["detail"]
        assert self.read(backup_host["database"]) == "original"

    def test_asking_for_what_the_archive_lacks_is_refused_with_a_reason(
        self, client, sudo_admin, backup_host
    ):
        made = client.post(
            "/api/backups", json={"include_env": False}, headers=auth(sudo_admin)
        ).json()["backups"][0]

        response = client.post(
            f"/api/backups/{made['name']}/restore",
            json={"items": ["env"]},
            headers=auth(sudo_admin),
        )

        assert response.status_code == 400
        assert "env" in response.json()["detail"]

    def test_restoring_nothing_is_refused_by_the_schema(self, client, sudo_admin, archive):
        response = client.post(
            f"/api/backups/{archive['name']}/restore", json={"items": []}, headers=auth(sudo_admin)
        )
        assert response.status_code == 422
