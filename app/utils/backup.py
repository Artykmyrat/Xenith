"""Backups of the panel: making them, importing them, putting one back.

A backup is one gzipped tar holding the database, the .env the panel was
started with, the xray configuration and the small files under the data
directory. Those are the same four things a Marzban backup carries, which is
what makes one importable here: nothing in this module depends on an archive
having been written by this panel, because the common case is exactly the one
where it was not.

Two things carry the weight. Nothing out of an uploaded archive is written to
the path the archive names — every member is classified first and then written
to a destination the panel picks, so a member called `../../etc/passwd` has
nowhere to go. And a restore always archives what it is about to replace,
under a `pre-restore-` name, because putting a database back is otherwise the
one action in the panel with nothing behind it.
"""

import gzip
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from typing import Dict, IO, List, Optional, Tuple

from sqlalchemy.engine.url import make_url

from app.utils.files import FileWriteError, atomic_write
from config import (BACKUP_DATA_DIR, BACKUP_DIR, BACKUP_ENABLED,
                    BACKUP_ENV_FILE, BACKUP_INTERVAL_HOURS, BACKUP_KEEP,
                    BACKUP_MAX_FILE_BYTES, BACKUP_MAX_UPLOAD_BYTES,
                    BACKUP_TIMEOUT, MYSQL_EXECUTABLE_PATH,
                    MYSQLDUMP_EXECUTABLE_PATH, SQLALCHEMY_DATABASE_URL,
                    XRAY_JSON)

# What the panel will accept as a backup file name. No slashes, so a name from
# a browser cannot point outside the backup directory.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Archive suffixes, longest first: the suffix decides how a file is opened.
TAR_SUFFIXES = (".tar.gz", ".tgz", ".tar")
ZIP_SUFFIXES = (".zip",)
# A bare database, which is what someone who copied one file off an old server
# usually has. Treated as an archive of one member.
SQLITE_SUFFIXES = (".sqlite3", ".sqlite", ".db")
SQL_SUFFIXES = (".sql", ".sql.gz")

ACCEPTED_SUFFIXES = TAR_SUFFIXES + ZIP_SUFFIXES + SQLITE_SUFFIXES + SQL_SUFFIXES

MANIFEST_NAME = "xenith-backup.json"
MANIFEST_VERSION = 1

# The first bytes of every SQLite database file. Checked before one is put in
# place, so an archive holding something else cannot take the panel down.
SQLITE_MAGIC = b"SQLite format 3\x00"

# Where the parts of our own archives live.
DB_MEMBER_SQLITE = "db/db.sqlite3"
DB_MEMBER_SQL = "db/dump.sql"
ENV_MEMBER = "env/.env"
XRAY_MEMBER = "xray/xray_config.json"
DATA_PREFIX = "data/"

# Directory names never archived: another database's data directory, and the
# backups themselves, which would nest one archive inside the next.
SKIPPED_DIRS = frozenset({"backups", "mysql", "mysql-data", "mariadb", "postgres", "pgdata", ".git"})

# Leading path segments an archive from another install carries that mean
# nothing here. Stripped so `marzban/certs/x.pem` lands beside our own certs.
KNOWN_PREFIXES = (
    "var/lib/marzban/",
    "var/lib/xenith/",
    "opt/marzban/",
    "opt/xenith/",
    "marzban-data/",
    "marzban/",
    "xenith/",
)

# Restorable items, in the order a restore applies them.
ITEMS = ("database", "env", "xray_config", "data")


class BackupError(Exception):
    """The request could not be carried out; the message is safe to show."""


@dataclass
class Database:
    """Where the live database is and whether the panel can dump it."""

    kind: str  # "sqlite", "mysql" or "other"
    path: Optional[str] = None  # sqlite file
    name: Optional[str] = None  # mysql database
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    password: Optional[str] = None
    # Why it cannot be dumped, when it cannot. None means it can.
    reason: Optional[str] = None


@dataclass
class BackupFile:
    name: str
    size: int
    created_at: float
    kind: str  # "manual", "automatic", "pre-restore" or "imported"
    source: str  # "xenith", "marzban" or "unknown"
    note: str = ""


@dataclass
class Entry:
    """One member of an archive."""

    path: str
    size: int


@dataclass
class Contents:
    """What an archive turned out to hold, and what of it applies here."""

    name: str
    format: str  # "tar", "zip", "sqlite" or "sql"
    source: str
    kind: str
    size: int
    created_at: float
    manifest: Optional[dict] = None
    database: Optional[str] = None  # "sqlite" or "sql", when there is one
    database_member: Optional[str] = None
    database_bytes: int = 0
    env_member: Optional[str] = None
    xray_member: Optional[str] = None
    data_files: int = 0
    data_bytes: int = 0
    entries: List[str] = field(default_factory=list)
    truncated: bool = False
    restorable: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class RestoreReport:
    applied: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    # The archive taken of what the restore replaced.
    safety_backup: Optional[str] = None
    restart_required: bool = False


# --- the live install --------------------------------------------------------


def is_enabled() -> bool:
    return bool(BACKUP_ENABLED)


def directory() -> str:
    return os.path.abspath(BACKUP_DIR)


def data_directory() -> str:
    return os.path.abspath(BACKUP_DATA_DIR)


def env_file() -> str:
    return os.path.abspath(BACKUP_ENV_FILE)


def xray_config_file() -> str:
    return os.path.abspath(XRAY_JSON)


def paths() -> Dict[str, str]:
    return {
        "backups": directory(),
        "data": data_directory(),
        "env": env_file(),
        "xray_config": xray_config_file(),
    }


def schedule() -> Dict[str, int]:
    return {"interval_hours": max(0, BACKUP_INTERVAL_HOURS), "keep": max(1, BACKUP_KEEP)}


def which(executable: str) -> Optional[str]:
    """The absolute path of a client tool, or None when it is not installed."""
    if os.path.isabs(executable):
        return executable if os.access(executable, os.X_OK) else None
    return shutil.which(executable)


def database() -> Database:
    """The live database, read off the connection URL the panel was given."""
    try:
        url = make_url(SQLALCHEMY_DATABASE_URL)
    except Exception as err:  # a URL SQLAlchemy will not parse
        return Database(kind="other", reason=f"Unrecognised database URL: {err}")

    driver = url.get_backend_name()

    if driver == "sqlite":
        if not url.database or url.database == ":memory:":
            return Database(kind="sqlite", reason="The database is in memory, so there is nothing to copy.")
        return Database(kind="sqlite", path=os.path.abspath(url.database))

    if driver in ("mysql", "mariadb"):
        return Database(
            kind="mysql",
            name=url.database,
            host=url.host or "127.0.0.1",
            port=url.port or 3306,
            user=url.username,
            password=url.password,
            reason=(
                None
                if which(MYSQLDUMP_EXECUTABLE_PATH)
                else f"{MYSQLDUMP_EXECUTABLE_PATH} is not installed, so this database cannot be dumped."
            ),
        )

    return Database(kind="other", reason=f"Backups do not cover a {driver} database.")


def writable() -> Tuple[bool, Optional[str]]:
    """Whether the backup directory exists and can be written, creating it."""
    if not is_enabled():
        return False, "Backups are turned off."
    target = directory()
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as err:
        return False, f"Could not create {target}: {err}"
    if not os.access(target, os.W_OK):
        return False, f"{target} is not writable."
    return True, None


def _require_directory() -> str:
    ok, reason = writable()
    if not ok:
        raise BackupError(reason)
    return directory()


# --- archives on disk --------------------------------------------------------


def _suffix_of(name: str) -> Optional[str]:
    lowered = name.lower()
    for suffix in sorted(ACCEPTED_SUFFIXES, key=len, reverse=True):
        if lowered.endswith(suffix):
            return suffix
    return None


def archive_path(name: str) -> str:
    """Resolve a backup name to its file, refusing anything that is not one."""
    if not NAME_RE.match(name or ""):
        raise BackupError("That is not a backup name.")
    if not _suffix_of(name):
        raise BackupError("That is not a backup file.")
    path = os.path.join(directory(), name)
    # The name pattern already excludes separators; this is the second lock.
    if os.path.dirname(os.path.abspath(path)) != directory():
        raise BackupError("That is not a backup name.")
    if not os.path.isfile(path):
        raise BackupError(f"{name} does not exist.")
    return path


def _kind_from_name(name: str) -> str:
    for prefix, kind in (
        ("pre-restore-", "pre-restore"),
        ("auto-", "automatic"),
        ("imported-", "imported"),
    ):
        if name.startswith(prefix):
            return kind
    return "manual"


def _peek_manifest(path: str) -> Optional[dict]:
    """Our manifest, without reading the whole archive.

    It is written first, so it is the first member of the tar and the read
    stops there. An archive from somewhere else has none, and this walks a few
    members before giving up rather than the whole file.
    """
    suffix = _suffix_of(path) or ""
    try:
        if suffix in TAR_SUFFIXES:
            with tarfile.open(path, "r:*") as tar:
                for _ in range(8):
                    member = tar.next()
                    if member is None:
                        return None
                    if os.path.basename(member.name) == MANIFEST_NAME and member.isfile():
                        handle = tar.extractfile(member)
                        return json.loads(handle.read(1_000_000).decode("utf-8")) if handle else None
        elif suffix in ZIP_SUFFIXES:
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist()[:64]:
                    if os.path.basename(name) == MANIFEST_NAME:
                        return json.loads(archive.read(name)[:1_000_000].decode("utf-8"))
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError, UnicodeDecodeError):
        return None
    return None


def _describe(path: str) -> BackupFile:
    name = os.path.basename(path)
    stat = os.stat(path)
    manifest = _peek_manifest(path)
    return BackupFile(
        name=name,
        size=stat.st_size,
        created_at=stat.st_mtime,
        kind=(manifest or {}).get("kind") or _kind_from_name(name),
        source="xenith" if manifest else "unknown",
        note=(manifest or {}).get("note") or "",
    )


def list_backups() -> List[BackupFile]:
    """Every archive in the backup directory, newest first."""
    target = directory()
    if not os.path.isdir(target):
        return []
    found = []
    for name in os.listdir(target):
        if not NAME_RE.match(name) or not _suffix_of(name):
            continue
        path = os.path.join(target, name)
        if not os.path.isfile(path):
            continue
        try:
            found.append(_describe(path))
        except OSError:
            continue
    return sorted(found, key=lambda backup: backup.created_at, reverse=True)


def delete_backup(name: str) -> None:
    path = archive_path(name)
    try:
        os.unlink(path)
    except OSError as err:
        raise BackupError(f"Could not delete {name}: {err}")


def prune(keep: Optional[int] = None) -> List[str]:
    """Drop the oldest automatic backups. Nothing else is ever pruned."""
    limit = max(1, keep if keep is not None else BACKUP_KEEP)
    automatic = [backup for backup in list_backups() if backup.kind == "automatic"]
    removed = []
    for backup in automatic[limit:]:
        try:
            os.unlink(os.path.join(directory(), backup.name))
            removed.append(backup.name)
        except OSError:
            continue
    return removed


# --- making one --------------------------------------------------------------


def _dump_sqlite(source: str, destination: str) -> None:
    """Copy a live SQLite database, consistently.

    The backup API takes the copy through SQLite itself rather than off the
    filesystem, so a write landing halfway through does not produce an archive
    holding half a transaction.
    """
    if not os.path.isfile(source):
        raise BackupError(f"The database file {source} does not exist.")
    try:
        with sqlite3.connect(source, timeout=30) as live, sqlite3.connect(destination) as copy:
            live.backup(copy)
    except sqlite3.Error as err:
        raise BackupError(f"Could not copy the database: {err}")


def _mysql_arguments(target: Database) -> List[str]:
    arguments = [f"--host={target.host}", f"--port={target.port}"]
    if target.user:
        arguments.append(f"--user={target.user}")
    return arguments


def _mysql_environment(target: Database) -> dict:
    """The password goes through the environment, never the command line."""
    environment = dict(os.environ)
    if target.password:
        environment["MYSQL_PWD"] = target.password
    else:
        environment.pop("MYSQL_PWD", None)
    return environment


def _dump_mysql(target: Database, destination: str) -> None:
    tool = which(MYSQLDUMP_EXECUTABLE_PATH)
    if not tool:
        raise BackupError(f"{MYSQLDUMP_EXECUTABLE_PATH} is not installed.")
    command = [
        tool,
        *_mysql_arguments(target),
        "--single-transaction",
        "--quick",
        "--routines",
        "--triggers",
        "--no-tablespaces",
        "--skip-lock-tables",
        f"--result-file={destination}",
        "--databases",
        target.name,
    ]
    try:
        result = subprocess.run(
            command,
            env=_mysql_environment(target),
            capture_output=True,
            text=True,
            timeout=BACKUP_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise BackupError(f"Could not run {MYSQLDUMP_EXECUTABLE_PATH}: {err}")
    if result.returncode != 0:
        raise BackupError(f"mysqldump failed: {(result.stderr or '').strip()[:400]}")


def _data_files() -> Tuple[List[Tuple[str, str]], List[str]]:
    """Small files under the data directory, as (absolute path, archive path).

    Anything large is data rather than configuration — geoip databases and
    core binaries live here too — and is reported as skipped instead.
    """
    root = data_directory()
    if not os.path.isdir(root):
        return [], []

    backups = directory()
    # The database usually lives under the data directory too. It is archived
    # through SQLite, into db/, so the file itself is left out here: a plain
    # copy of a database being written to is a torn one, and restoring `data`
    # would put it back over the good copy.
    live = database()
    excluded = set()
    if live.path:
        excluded = {live.path, live.path + "-wal", live.path + "-shm", live.path + "-journal"}

    files: List[Tuple[str, str]] = []
    skipped: List[str] = []

    for folder, subfolders, names in os.walk(root):
        # Pruned in place so os.walk does not descend into them at all.
        subfolders[:] = [
            name
            for name in subfolders
            if name not in SKIPPED_DIRS and os.path.abspath(os.path.join(folder, name)) != backups
        ]
        for name in names:
            path = os.path.join(folder, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            if os.path.abspath(path) in excluded:
                continue
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > BACKUP_MAX_FILE_BYTES:
                skipped.append(f"{relative} ({size} bytes)")
                continue
            files.append((path, DATA_PREFIX + relative))

    files.sort(key=lambda pair: pair[1])
    return files, skipped


def create_backup(
    kind: str = "manual",
    include_database: bool = True,
    include_env: bool = True,
    include_xray_config: bool = True,
    include_data: bool = True,
    note: str = "",
) -> BackupFile:
    """Write one archive and return what it turned out to be."""
    target_dir = _require_directory()
    target = database()

    items: List[str] = []
    skipped: List[str] = []
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    prefix = {"automatic": "auto-", "pre-restore": "pre-restore-"}.get(kind, "")
    name = f"{prefix}xenith-{stamp}.tar.gz"
    path = os.path.join(target_dir, name)
    if os.path.exists(path):
        name = f"{prefix}xenith-{stamp}-{int(time.time() * 1000) % 1000:03d}.tar.gz"
        path = os.path.join(target_dir, name)

    staging = tempfile.mkdtemp(prefix=".xenith-backup-", dir=target_dir)
    database_member = None
    try:
        if include_database:
            if target.reason:
                skipped.append(f"database ({target.reason})")
            elif target.kind == "sqlite":
                database_member = DB_MEMBER_SQLITE
                _dump_sqlite(target.path, os.path.join(staging, "db.sqlite3"))
                items.append("database")
            elif target.kind == "mysql":
                database_member = DB_MEMBER_SQL
                _dump_mysql(target, os.path.join(staging, "dump.sql"))
                items.append("database")

        environment = env_file()
        if include_env and os.path.isfile(environment):
            items.append("env")
        elif include_env:
            skipped.append(f"env ({environment} does not exist)")

        xray = xray_config_file()
        if include_xray_config and os.path.isfile(xray):
            items.append("xray_config")
        elif include_xray_config:
            skipped.append(f"xray_config ({xray} does not exist)")

        data, data_skipped = _data_files() if include_data else ([], [])
        skipped.extend(data_skipped)
        if data:
            items.append("data")

        manifest = {
            "format": "xenith-backup",
            "version": MANIFEST_VERSION,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "panel_version": _panel_version(),
            "kind": kind,
            "note": (note or "").strip()[:200],
            "database": {
                "kind": target.kind,
                "member": database_member,
                "source": target.path or target.name,
            }
            if database_member
            else None,
            "items": items,
            "skipped": skipped,
        }

        manifest_path = os.path.join(staging, MANIFEST_NAME)
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)

        # Written to a temporary name and renamed, so a half-written archive is
        # never listed as one that could be restored.
        partial = path + ".part"
        try:
            with tarfile.open(partial, "w:gz") as tar:
                # The manifest goes first: listing a backup then costs one
                # member's worth of reading rather than the whole file.
                tar.add(manifest_path, arcname=MANIFEST_NAME)
                if database_member:
                    tar.add(
                        os.path.join(staging, os.path.basename(database_member)),
                        arcname=database_member,
                    )
                if "env" in items:
                    tar.add(environment, arcname=ENV_MEMBER)
                if "xray_config" in items:
                    tar.add(xray, arcname=XRAY_MEMBER)
                for source, arcname in data:
                    try:
                        tar.add(source, arcname=arcname)
                    except OSError:
                        # A file that vanished or cannot be read mid-walk is
                        # not worth losing the rest of the backup over.
                        continue
            os.replace(partial, path)
        except (OSError, tarfile.TarError) as err:
            if os.path.exists(partial):
                os.unlink(partial)
            raise BackupError(f"Could not write the archive: {err}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return _describe(path)


def _panel_version() -> str:
    from app import __version__  # noqa: circular at import time

    return __version__


# --- reading one -------------------------------------------------------------


class _Archive:
    """One archive, opened for reading, whatever shape it arrived in."""

    def __init__(self, path: str):
        self.path = path
        self.format = "tar"
        self._tar: Optional[tarfile.TarFile] = None
        self._zip: Optional[zipfile.ZipFile] = None
        self._bare: Optional[Entry] = None

        suffix = _suffix_of(path) or ""
        try:
            if suffix in TAR_SUFFIXES:
                self._tar = tarfile.open(path, "r:*")
            elif suffix in ZIP_SUFFIXES:
                self.format = "zip"
                self._zip = zipfile.ZipFile(path)
            else:
                self.format = "sqlite" if suffix in SQLITE_SUFFIXES else "sql"
                self._bare = Entry(path=os.path.basename(path), size=os.path.getsize(path))
        except (OSError, tarfile.TarError, zipfile.BadZipFile) as err:
            raise BackupError(f"The archive could not be opened: {err}")

    def close(self) -> None:
        for handle in (self._tar, self._zip):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass

    def __enter__(self) -> "_Archive":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def entries(self) -> List[Entry]:
        if self._bare is not None:
            return [self._bare]
        try:
            if self._tar is not None:
                return [
                    Entry(path=member.name, size=member.size)
                    for member in self._tar.getmembers()
                    if member.isfile()
                ]
            return [
                Entry(path=info.filename, size=info.file_size)
                for info in self._zip.infolist()
                if not info.is_dir()
            ]
        except (OSError, tarfile.TarError, zipfile.BadZipFile) as err:
            raise BackupError(f"The archive could not be read: {err}")

    def open(self, path: str) -> IO[bytes]:
        try:
            if self._bare is not None:
                return open(self.path, "rb")
            if self._tar is not None:
                handle = self._tar.extractfile(path)
                if handle is None:
                    raise BackupError(f"{path} is not a file in the archive.")
                return handle
            return self._zip.open(path)
        except (OSError, KeyError, tarfile.TarError, zipfile.BadZipFile) as err:
            raise BackupError(f"{path} could not be read from the archive: {err}")

    def read(self, path: str, limit: int = 4 * 1024 * 1024) -> bytes:
        with self.open(path) as handle:
            return handle.read(limit)

    def extract(self, path: str, destination: str, limit: int) -> int:
        """Copy one member out, refusing to write more than `limit` bytes."""
        written = 0
        with self.open(path) as source, open(destination, "wb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise BackupError(f"{path} is larger than the {limit} byte limit.")
                target.write(chunk)
        return written


def _normalise(path: str) -> str:
    """An archive member's path, reduced to something relative and harmless.

    Empty when the member points outside the tree it came in — an absolute
    path, or one that climbs out with `..`. Nothing is written by this path
    anyway; this is what keeps it from being written at all.
    """
    cleaned = (path or "").replace("\\", "/").strip()
    if not cleaned or cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        return ""
    segments = []
    for segment in cleaned.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            return ""
        segments.append(segment)
    return "/".join(segments)


def _strip_prefix(path: str, common: str) -> str:
    """Drop the wrapper directories another install's archive carries."""
    if common and path.startswith(common + "/"):
        path = path[len(common) + 1:]
    for prefix in KNOWN_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _common_directory(entries: List[Entry]) -> str:
    """The single top-level directory every member sits under, if there is one."""
    tops = set()
    for entry in entries:
        normalised = _normalise(entry.path)
        if not normalised:
            continue
        head, _, tail = normalised.partition("/")
        if not tail:
            return ""  # a file at the root, so there is no single wrapper
        tops.add(head)
        if len(tops) > 1:
            return ""
    return tops.pop() if len(tops) == 1 else ""


@dataclass
class _Plan:
    """What each member of an archive is, worked out once and reused."""

    sqlite_member: Optional[str] = None
    sqlite_size: int = 0
    sqlite_rank: int = -1
    sql_member: Optional[str] = None
    sql_size: int = 0
    sql_rank: int = -1
    env_member: Optional[str] = None
    xray_member: Optional[str] = None
    # (member, destination relative to the data directory)
    data: List[Tuple[str, str, int]] = field(default_factory=list)
    manifest: Optional[dict] = None


def _database_rank(relative: str, base: str) -> int:
    """How much one member looks like the database an archive is built around.

    An archive can hold more than one file that ends in .sqlite3 — a copy
    somebody left in the data directory, say — so the one in the slot a backup
    puts its database in wins over one that merely has the right name.
    """
    if relative.startswith("db/"):
        return 3
    if base in ("db.sqlite3", "dump.sql", "marzban.db"):
        return 2
    if "/" not in relative:
        return 1
    return 0


def _better_database(relative: str, base: str, current: Optional[str], rank: int) -> Optional[bool]:
    """True when this member should replace the one already picked."""
    return True if current is None or _database_rank(relative, base) > rank else None


def _plan(archive: _Archive) -> _Plan:
    entries = archive.entries()
    common = _common_directory(entries)
    plan = _Plan()

    for entry in entries:
        normalised = _normalise(entry.path)
        if not normalised:
            continue
        base = os.path.basename(normalised).lower()
        relative = _strip_prefix(normalised, common)

        if base == MANIFEST_NAME:
            try:
                plan.manifest = json.loads(archive.read(entry.path, 1_000_000).decode("utf-8"))
            except (BackupError, ValueError, UnicodeDecodeError):
                plan.manifest = None
            continue
        if base.endswith(SQLITE_SUFFIXES):
            if _better_database(relative, base, plan.sqlite_member, plan.sqlite_rank) is not None:
                plan.sqlite_member, plan.sqlite_size = entry.path, entry.size
                plan.sqlite_rank = _database_rank(relative, base)
            continue
        if base.endswith(SQL_SUFFIXES):
            if _better_database(relative, base, plan.sql_member, plan.sql_rank) is not None:
                plan.sql_member, plan.sql_size = entry.path, entry.size
                plan.sql_rank = _database_rank(relative, base)
            continue
        if base == ".env" and plan.env_member is None:
            plan.env_member = entry.path
            continue
        if base == "xray_config.json" and plan.xray_member is None:
            plan.xray_member = entry.path
            continue

        destination = relative[len(DATA_PREFIX):] if relative.startswith(DATA_PREFIX) else relative
        destination = _normalise(destination)
        if destination:
            plan.data.append((entry.path, destination, entry.size))

    return plan


def inspect(name: str) -> Contents:
    """Open an archive and report what it holds and what of it applies here."""
    path = archive_path(name)
    stat = os.stat(path)
    live = database()

    with _Archive(path) as archive:
        plan = _plan(archive)
        entries = archive.entries()
        format_name = archive.format

    listed = [entry.path for entry in entries][:200]
    contents = Contents(
        name=name,
        format=format_name,
        source="xenith" if plan.manifest else ("marzban" if (plan.env_member or plan.xray_member or plan.sqlite_member or plan.sql_member) else "unknown"),
        kind=(plan.manifest or {}).get("kind") or _kind_from_name(name),
        size=stat.st_size,
        created_at=stat.st_mtime,
        manifest=plan.manifest,
        env_member=plan.env_member,
        xray_member=plan.xray_member,
        data_files=len(plan.data),
        data_bytes=sum(size for _, _, size in plan.data),
        entries=listed,
        truncated=len(entries) > len(listed),
    )

    # Which of the two database shapes applies depends on what this panel runs,
    # not on what the archive happens to carry first.
    if live.kind == "sqlite" and plan.sqlite_member:
        contents.database, contents.database_member, contents.database_bytes = (
            "sqlite",
            plan.sqlite_member,
            plan.sqlite_size,
        )
    elif live.kind == "mysql" and plan.sql_member:
        contents.database, contents.database_member, contents.database_bytes = (
            "sql",
            plan.sql_member,
            plan.sql_size,
        )
    elif plan.sqlite_member or plan.sql_member:
        held = "SQLite database" if plan.sqlite_member else "SQL dump"
        runs = {"sqlite": "SQLite", "mysql": "MySQL"}.get(live.kind, live.kind)
        contents.warnings.append(
            f"The archive holds a {held}, but this panel runs on {runs}. "
            "The database in it cannot be restored here; move the data across manually."
        )

    if contents.database:
        if contents.database == "sql" and not which(MYSQL_EXECUTABLE_PATH):
            contents.warnings.append(
                f"{MYSQL_EXECUTABLE_PATH} is not installed, so the dump cannot be loaded from here."
            )
        else:
            contents.restorable.append("database")
    if contents.env_member:
        contents.restorable.append("env")
    if contents.xray_member:
        contents.restorable.append("xray_config")
    if contents.data_files:
        contents.restorable.append("data")

    if not contents.restorable:
        contents.warnings.append("Nothing in this archive can be restored here.")

    return contents


# --- importing one -----------------------------------------------------------


def store_upload(filename: str, content: bytes) -> BackupFile:
    """Keep an uploaded archive, under a name of the panel's own making."""
    target_dir = _require_directory()
    if not content:
        raise BackupError("The uploaded file is empty.")
    if len(content) > BACKUP_MAX_UPLOAD_BYTES:
        raise BackupError(f"The file is larger than the {BACKUP_MAX_UPLOAD_BYTES} byte limit.")

    suffix = _suffix_of(filename or "")
    if not suffix:
        raise BackupError(
            "Upload a .tar.gz, .zip, .sqlite3 or .sql file — that is what a backup arrives as."
        )

    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(filename)[: -len(suffix)]).strip("-.")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    name = f"imported-{stamp}-{stem or 'backup'}{suffix}"[:128]
    if not NAME_RE.match(name):
        name = f"imported-{stamp}{suffix}"
    path = os.path.join(target_dir, name)

    try:
        atomic_write(path, content, mode=0o600)
    except FileWriteError as err:
        raise BackupError(str(err))

    # An archive that cannot be opened is not kept: it would otherwise sit in
    # the list looking restorable until someone tried it.
    try:
        inspect(name)
    except BackupError:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise

    return _describe(path)


# --- putting one back --------------------------------------------------------


def _restore_sqlite(archive: _Archive, member: str, target: Database) -> None:
    if not target.path:
        raise BackupError(target.reason or "This panel has no SQLite database file to replace.")

    directory_of = os.path.dirname(target.path) or "."
    handle = tempfile.NamedTemporaryFile(
        "wb", dir=directory_of, prefix=".xenith-restore-", delete=False, suffix=".sqlite3"
    )
    handle.close()
    try:
        archive.extract(member, handle.name, BACKUP_MAX_UPLOAD_BYTES)
        with open(handle.name, "rb") as check:
            if check.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
                raise BackupError("That file is not a SQLite database.")
        # Anything the copy could not carry is checked before it is put in
        # place, because after the rename there is no going back to it.
        try:
            with sqlite3.connect(handle.name) as probe:
                probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.Error as err:
            raise BackupError(f"The database in the archive could not be opened: {err}")

        _dispose_engine()
        os.chmod(handle.name, 0o600)
        os.replace(handle.name, target.path)
        # The journal beside the old file describes an inode that is no longer
        # there; left behind, SQLite would try to replay it over the new one.
        for sidecar in (target.path + "-wal", target.path + "-shm", target.path + "-journal"):
            try:
                os.unlink(sidecar)
            except OSError:
                pass
        _dispose_engine()
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _dispose_engine() -> None:
    """Close every pooled connection, so the next one opens the new file."""
    try:
        from app.db.base import engine  # noqa: circular at import time

        engine.dispose()
    except Exception:
        pass


def _restore_mysql(archive: _Archive, member: str, target: Database) -> None:
    tool = which(MYSQL_EXECUTABLE_PATH)
    if not tool:
        raise BackupError(f"{MYSQL_EXECUTABLE_PATH} is not installed, so a dump cannot be loaded.")

    handle = tempfile.NamedTemporaryFile("wb", prefix="xenith-restore-", suffix=".sql", delete=False)
    handle.close()
    try:
        archive.extract(member, handle.name, BACKUP_MAX_UPLOAD_BYTES)
        # A dump that arrived gzipped is unpacked here rather than piped, so
        # the client below always reads plain SQL.
        if member.lower().endswith(".gz"):
            plain = handle.name + ".plain"
            with gzip.open(handle.name, "rb") as source, open(plain, "wb") as sink:
                shutil.copyfileobj(source, sink)
            os.replace(plain, handle.name)

        command = [tool, *_mysql_arguments(target)]
        if target.name:
            command.append(target.name)
        with open(handle.name, "rb") as dump:
            result = subprocess.run(
                command,
                stdin=dump,
                env=_mysql_environment(target),
                capture_output=True,
                text=True,
                timeout=BACKUP_TIMEOUT,
            )
        if result.returncode != 0:
            raise BackupError(f"The dump was refused: {(result.stderr or '').strip()[:400]}")
        _dispose_engine()
    except (OSError, subprocess.SubprocessError) as err:
        raise BackupError(f"Could not run {MYSQL_EXECUTABLE_PATH}: {err}")
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def _restore_file(archive: _Archive, member: str, destination: str) -> None:
    parent = os.path.dirname(destination) or "."
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as err:
        raise BackupError(f"Could not create {parent}: {err}")
    content = archive.read(member, BACKUP_MAX_FILE_BYTES + 1)
    if len(content) > BACKUP_MAX_FILE_BYTES:
        raise BackupError(f"{member} is larger than the {BACKUP_MAX_FILE_BYTES} byte limit.")
    try:
        atomic_write(destination, content, mode=0o600 if os.path.basename(destination) == ".env" else 0o644)
    except FileWriteError as err:
        raise BackupError(str(err))


def restore(name: str, items: List[str], safety_backup: bool = True) -> RestoreReport:
    """Apply the chosen parts of one archive to this install."""
    wanted = [item for item in ITEMS if item in set(items or [])]
    if not wanted:
        raise BackupError("Choose at least one thing to restore.")

    contents = inspect(name)
    unavailable = [item for item in wanted if item not in contents.restorable]
    if unavailable:
        raise BackupError(
            f"This archive cannot restore: {', '.join(unavailable)}. "
            + (contents.warnings[0] if contents.warnings else "")
        )

    report = RestoreReport()
    if safety_backup:
        try:
            report.safety_backup = create_backup(kind="pre-restore", note=f"before restoring {name}").name
        except BackupError as err:
            raise BackupError(f"Nothing was restored: the safety backup failed first ({err}).")

    path = archive_path(name)
    live = database()
    root = data_directory()

    with _Archive(path) as archive:
        plan = _plan(archive)

        if "database" in wanted:
            if contents.database == "sqlite":
                _restore_sqlite(archive, contents.database_member, live)
            else:
                _restore_mysql(archive, contents.database_member, live)
            report.applied.append("database")
            report.restart_required = True

        if "env" in wanted:
            _restore_file(archive, plan.env_member, env_file())
            report.applied.append("env")
            report.restart_required = True

        if "xray_config" in wanted:
            _restore_file(archive, plan.xray_member, xray_config_file())
            report.applied.append("xray_config")
            report.restart_required = True

        if "data" in wanted:
            written = 0
            budget = BACKUP_MAX_UPLOAD_BYTES
            for member, relative, size in plan.data:
                destination = os.path.abspath(os.path.join(root, relative))
                # The destination is built from a normalised relative path, so
                # this should hold by construction; it is checked because the
                # cost of it not holding is a file written anywhere on disk.
                if os.path.commonpath([destination, root]) != root:
                    report.skipped.append(relative)
                    continue
                if size > BACKUP_MAX_FILE_BYTES or written + size > budget:
                    report.skipped.append(relative)
                    continue
                try:
                    _restore_file(archive, member, destination)
                    written += size
                except BackupError:
                    report.skipped.append(relative)
            report.applied.append("data")
            report.restart_required = True

    return report
