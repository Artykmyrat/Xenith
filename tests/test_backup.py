"""Making, reading and restoring backup archives.

The tests care about the two things the module exists for: that an archive
made here comes back exactly as it went in, and that an archive made somewhere
else — a Marzban one, with its own directory layout and no manifest — is
understood well enough to be restored from. The rest is refusal: an archive
member cannot write outside the data directory, however it is named.
"""

import os
import sqlite3
import tarfile
import zipfile

import pytest

from app.utils import backup


def marzban_archive(path, tree, prefix="marzban/var/lib/marzban"):
    """An archive shaped the way a backup taken off a Marzban host is.

    Nothing in it is named the way ours are: the paths carry the host's own
    directories, and there is no manifest to read.
    """
    source = tree["root"] / "old.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    connection.execute("INSERT INTO users VALUES (1, 'from-marzban')")
    connection.commit()
    connection.close()

    with tarfile.open(path, "w:gz") as tar:
        tar.add(source, arcname=f"{prefix}/db.sqlite3")
        tar.add(tree["env"], arcname="marzban/opt/marzban/.env")
        tar.add(tree["xray_config"], arcname=f"{prefix}/xray_config.json")
        tar.add(tree["data"] / "certs" / "panel.pem", arcname=f"{prefix}/certs/old.pem")
    return path


class TestStatus:
    def test_the_database_is_read_off_the_connection_url(self, backup_host):
        target = backup.database()
        assert target.kind == "sqlite"
        assert target.path == str(backup_host["database"])
        assert target.reason is None

    def test_the_directory_is_created_on_demand(self, backup_host):
        assert not backup_host["backups"].exists()
        assert backup.writable() == (True, None)
        assert backup_host["backups"].is_dir()

    def test_turning_backups_off_makes_the_directory_unusable(self, backup_host, monkeypatch):
        monkeypatch.setattr(backup, "BACKUP_ENABLED", False)
        ok, reason = backup.writable()
        assert not ok and "off" in reason

    def test_a_password_never_leaves_the_url(self, backup_host, monkeypatch):
        monkeypatch.setattr(
            backup, "SQLALCHEMY_DATABASE_URL", "mysql+pymysql://root:secret@127.0.0.1:3306/xenith"
        )
        target = backup.database()
        assert target.kind == "mysql"
        assert target.name == "xenith"
        # The password is carried for the client tools, and nothing else here
        # ever puts it in a response: the router sends `name`, not the URL.
        assert target.password == "secret"


class TestCreating:
    def test_a_backup_holds_the_four_things(self, backup_host):
        made = backup.create_backup(note="nightly")

        assert made.kind == "manual" and made.source == "xenith" and made.note == "nightly"
        found = backup.inspect(made.name)
        assert found.database == "sqlite"
        assert found.env_member and found.xray_member
        assert found.data_files == 1  # the certificate; the database has its own slot
        assert found.restorable == ["database", "env", "xray_config", "data"]

    def test_what_is_left_out_stays_out(self, backup_host):
        made = backup.create_backup(include_env=False, include_data=False)
        found = backup.inspect(made.name)
        assert found.env_member is None
        assert found.data_files == 0
        assert found.restorable == ["database", "xray_config"]

    def test_the_backup_directory_is_never_archived_into_itself(self, backup_host, monkeypatch):
        # The archives live under the data directory on a real install, which
        # is the case that would otherwise nest each backup inside the next.
        monkeypatch.setattr(backup, "BACKUP_DIR", str(backup_host["data"] / "backups"))
        first = backup.create_backup()
        second = backup.create_backup()

        with tarfile.open(os.path.join(backup.directory(), second.name)) as tar:
            names = tar.getnames()
        assert not any(first.name in name for name in names)

    def test_a_large_file_is_skipped_and_said_so(self, backup_host, monkeypatch):
        (backup_host["data"] / "geoip.dat").write_bytes(b"0" * 2048)
        monkeypatch.setattr(backup, "BACKUP_MAX_FILE_BYTES", 1024)

        made = backup.create_backup()
        found = backup.inspect(made.name)
        assert any("geoip.dat" in entry for entry in found.manifest["skipped"])
        assert not any("geoip.dat" in entry for entry in found.entries)

    def test_the_copy_is_taken_through_sqlite(self, backup_host, db_value):
        made = backup.create_backup()
        with backup._Archive(backup.archive_path(made.name)) as archive:
            archive.extract(backup.DB_MEMBER_SQLITE, str(backup_host["root"] / "out.sqlite3"), 10**7)
        assert db_value(backup_host["root"] / "out.sqlite3") == "original"

    def test_a_database_that_cannot_be_dumped_is_reported_not_raised(self, backup_host, monkeypatch):
        # A MySQL install without the client tools installed. The other three
        # things are still worth archiving, so the backup is still made.
        monkeypatch.setattr(
            backup, "SQLALCHEMY_DATABASE_URL", "mysql+pymysql://root:secret@127.0.0.1/xenith"
        )
        monkeypatch.setattr(backup, "which", lambda executable: None)

        made = backup.create_backup()

        found = backup.inspect(made.name)
        assert found.manifest["database"] is None
        assert found.manifest["items"] == ["env", "xray_config", "data"]
        assert any("mysqldump" in entry for entry in found.manifest["skipped"])

    def test_the_live_database_file_is_not_archived_twice(self, backup_host):
        # It lives under the data directory, and a plain copy of a database
        # being written to is a torn one. Only the copy SQLite took counts.
        made = backup.create_backup()
        found = backup.inspect(made.name)
        assert found.database_member == backup.DB_MEMBER_SQLITE
        assert [entry for entry in found.entries if entry.startswith("data/")] == [
            "data/certs/panel.pem"
        ]


class TestListing:
    def test_newest_first(self, backup_host):
        first = backup.create_backup()
        second = backup.create_backup()
        os.utime(os.path.join(backup.directory(), second.name), (2_000_000_000, 2_000_000_000))
        assert [item.name for item in backup.list_backups()] == [second.name, first.name]

    def test_only_automatic_backups_are_pruned(self, backup_host):
        kept = [backup.create_backup() for _ in range(2)]
        automatic = [backup.create_backup(kind="automatic") for _ in range(3)]
        for index, item in enumerate(automatic):
            os.utime(os.path.join(backup.directory(), item.name), (1_000_000 + index, 1_000_000 + index))

        removed = backup.prune(keep=1)

        names = {item.name for item in backup.list_backups()}
        assert removed == [automatic[1].name, automatic[0].name]
        assert all(item.name in names for item in kept)

    def test_a_name_that_is_not_one_is_refused(self, backup_host):
        backup.create_backup()
        for name in ("../../etc/passwd", "/etc/passwd", "", "missing.tar.gz", "notes.txt"):
            with pytest.raises(backup.BackupError):
                backup.archive_path(name)


class TestImporting:
    def test_a_marzban_archive_is_understood_without_a_manifest(self, backup_host):
        path = marzban_archive(backup_host["root"] / "marzban.tar.gz", backup_host)
        stored = backup.store_upload("backup-2024.tar.gz", path.read_bytes())

        found = backup.inspect(stored.name)
        assert stored.kind == "imported"
        assert found.source == "marzban"
        assert found.database == "sqlite"
        assert found.env_member and found.xray_member
        assert found.data_files == 1
        assert found.restorable == ["database", "env", "xray_config", "data"]

    def test_a_bare_database_file_is_a_backup_too(self, backup_host):
        stored = backup.store_upload("db.sqlite3", backup_host["database"].read_bytes())
        found = backup.inspect(stored.name)
        assert found.format == "sqlite"
        assert found.restorable == ["database"]

    def test_a_zip_is_read_the_same_way(self, backup_host):
        path = backup_host["root"] / "backup.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.write(backup_host["database"], "marzban/db.sqlite3")
            archive.write(backup_host["env"], "marzban/.env")
        stored = backup.store_upload("backup.zip", path.read_bytes())

        found = backup.inspect(stored.name)
        assert found.format == "zip"
        assert found.restorable == ["database", "env"]

    def test_something_that_is_not_an_archive_is_not_kept(self, backup_host):
        with pytest.raises(backup.BackupError):
            backup.store_upload("holiday.jpg", b"not an archive")
        with pytest.raises(backup.BackupError):
            backup.store_upload("backup.tar.gz", b"not an archive")
        assert backup.list_backups() == []

    def test_an_empty_upload_is_refused(self, backup_host):
        with pytest.raises(backup.BackupError):
            backup.store_upload("backup.tar.gz", b"")

    def test_an_upload_over_the_limit_is_refused(self, backup_host, monkeypatch):
        monkeypatch.setattr(backup, "BACKUP_MAX_UPLOAD_BYTES", 10)
        with pytest.raises(backup.BackupError):
            backup.store_upload("backup.tar.gz", b"01234567890123")

    def test_a_dump_for_another_engine_is_named_as_unrestorable(self, backup_host):
        path = backup_host["root"] / "mysql.tar.gz"
        dump = backup_host["root"] / "dump.sql"
        dump.write_text("CREATE TABLE users (id INT);")
        with tarfile.open(path, "w:gz") as tar:
            tar.add(dump, arcname="marzban/db/dump.sql")
        stored = backup.store_upload("mysql.tar.gz", path.read_bytes())

        found = backup.inspect(stored.name)
        assert found.database is None
        assert "database" not in found.restorable
        assert "SQL dump" in found.warnings[0]


class TestRestoring:
    def change(self, path, value="changed"):
        connection = sqlite3.connect(str(path))
        connection.execute("UPDATE users SET name = ?", (value,))
        connection.commit()
        connection.close()

    def test_the_database_comes_back(self, backup_host, db_value):
        made = backup.create_backup()
        self.change(backup_host["database"])

        report = backup.restore(made.name, ["database"])

        assert report.applied == ["database"]
        assert report.restart_required
        assert db_value(backup_host["database"]) == "original"

    def test_what_the_restore_replaced_is_kept_first(self, backup_host, db_value):
        made = backup.create_backup()
        self.change(backup_host["database"], "second")

        report = backup.restore(made.name, ["database"])

        assert report.safety_backup and report.safety_backup.startswith("pre-restore-")
        # The way back from the wrong restore is the backup it took first.
        backup.restore(report.safety_backup, ["database"])
        assert db_value(backup_host["database"]) == "second"

    def test_a_marzban_database_lands_on_this_install(self, backup_host, db_value):
        path = marzban_archive(backup_host["root"] / "marzban.tar.gz", backup_host)
        stored = backup.store_upload("marzban.tar.gz", path.read_bytes())

        report = backup.restore(stored.name, ["database", "data"])

        assert report.applied == ["database", "data"]
        assert db_value(backup_host["database"]) == "from-marzban"
        # The host's own directories are stripped off on the way in.
        assert (backup_host["data"] / "certs" / "old.pem").read_text() == "certificate"

    def test_the_stale_journal_beside_the_old_database_goes(self, backup_host):
        made = backup.create_backup()
        sidecar = backup_host["database"].with_name(backup_host["database"].name + "-wal")
        sidecar.write_bytes(b"stale")

        backup.restore(made.name, ["database"])

        assert not sidecar.exists()

    def test_something_that_is_not_a_database_never_replaces_one(self, backup_host, db_value):
        path = backup_host["root"] / "fake.tar.gz"
        impostor = backup_host["root"] / "db.sqlite3"
        impostor.write_text("this is not a database")
        with tarfile.open(path, "w:gz") as tar:
            tar.add(impostor, arcname="marzban/db.sqlite3")
        stored = backup.store_upload("fake.tar.gz", path.read_bytes())

        with pytest.raises(backup.BackupError, match="not a SQLite database"):
            backup.restore(stored.name, ["database"])

        assert db_value(backup_host["database"]) == "original"

    def test_a_member_cannot_write_outside_the_data_directory(self, backup_host):
        path = backup_host["root"] / "escape.tar.gz"
        payload = backup_host["root"] / "payload"
        payload.write_text("owned")
        with tarfile.open(path, "w:gz") as tar:
            for arcname in (
                "marzban/../../../../etc/passwd",
                "marzban/certs/../../../escaped.pem",
                "/etc/shadow",
                "marzban/certs/ok.pem",
            ):
                tar.add(payload, arcname=arcname)
        stored = backup.store_upload("escape.tar.gz", path.read_bytes())

        found = backup.inspect(stored.name)
        # The two that climb out of the archive are dropped while it is read.
        # `/etc/shadow` is not one of them — tar itself stored it relative —
        # and it lands under the data directory like everything else, which is
        # what the destination being the panel's choice buys.
        assert found.data_files == 2

        backup.restore(stored.name, ["data"])

        assert (backup_host["data"] / "certs" / "ok.pem").read_text() == "owned"
        assert (backup_host["data"] / "etc" / "shadow").read_text() == "owned"
        assert not (backup_host["root"] / "escaped.pem").exists()
        assert not (backup_host["root"].parent / "escaped.pem").exists()

    def test_restoring_nothing_is_refused(self, backup_host):
        made = backup.create_backup()
        with pytest.raises(backup.BackupError):
            backup.restore(made.name, [])

    def test_asking_for_what_the_archive_lacks_is_refused(self, backup_host, db_value):
        made = backup.create_backup(include_env=False)
        with pytest.raises(backup.BackupError, match="env"):
            backup.restore(made.name, ["database", "env"])
        # Refused before anything was applied, safety backup included.
        assert [item.kind for item in backup.list_backups()] == ["manual"]

    def test_the_environment_file_is_replaced_whole(self, backup_host):
        made = backup.create_backup()
        backup_host["env"].write_text("SUDO_USERNAME=someone-else\n")

        backup.restore(made.name, ["env"])

        assert backup_host["env"].read_text() == "SUDO_USERNAME=admin\n"
        assert oct(backup_host["env"].stat().st_mode)[-3:] == "600"
