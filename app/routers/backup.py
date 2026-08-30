"""Making, importing and restoring backups of the panel.

Sudo only, and every one of these is an admin action with weight: a restore
replaces the database this panel is running on. The router keeps the shape it
shares with the rest of the panel — the current status rides along with every
change, so the dashboard never has to ask twice — and leaves the judgement to
app.utils.backup, which is where the archive handling lives.
"""

import os
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile
from fastapi.responses import FileResponse

from app.models.admin import Admin
from app.models.backup import (BackupContents, BackupCreate, BackupDatabase,
                               BackupFile, BackupRestore, BackupRestoreResult,
                               BackupSchedule, BackupStatus)
from app.utils import backup, responses

router = APIRouter(tags=["Backup"], prefix="/api/backups", responses={401: responses._401})

NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


def _when(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, timezone.utc)


def _file(item: backup.BackupFile) -> BackupFile:
    return BackupFile(
        name=item.name,
        size=item.size,
        created_at=_when(item.created_at),
        kind=item.kind,
        source=item.source,
        note=item.note,
    )


def _status() -> BackupStatus:
    ok, reason = backup.writable()
    target = backup.database()
    backups = backup.list_backups() if ok else []
    return BackupStatus(
        enabled=backup.is_enabled(),
        writable=ok,
        reason=reason,
        paths=backup.paths(),
        database=BackupDatabase(
            kind=target.kind,
            # The file path or the database name — never the URL, which
            # carries the password.
            target=target.path or target.name,
            reason=target.reason,
        ),
        schedule=BackupSchedule(**backup.schedule()),
        total_bytes=sum(item.size for item in backups),
        backups=[_file(item) for item in backups],
    )


def _guard(work):
    """Run one backup operation, turning its failures into a 400."""
    try:
        return work()
    except backup.BackupError as err:
        raise HTTPException(status_code=400, detail=str(err))


def _contents(found: backup.Contents) -> BackupContents:
    return BackupContents(
        name=found.name,
        format=found.format,
        source=found.source,
        kind=found.kind,
        size=found.size,
        created_at=_when(found.created_at),
        manifest=found.manifest,
        database=found.database,
        database_member=found.database_member,
        database_bytes=found.database_bytes,
        env_member=found.env_member,
        xray_member=found.xray_member,
        data_files=found.data_files,
        data_bytes=found.data_bytes,
        entries=found.entries,
        truncated=found.truncated,
        restorable=found.restorable,
        warnings=found.warnings,
    )


@router.get("", response_model=BackupStatus, responses={403: responses._403})
def get_backups(admin: Admin = Depends(Admin.check_sudo_admin)):
    """Every archive the panel holds, and whether it can write another."""
    return _status()


@router.post("", response_model=BackupStatus, responses={400: responses._400, 403: responses._403})
def create_backup(body: BackupCreate, admin: Admin = Depends(Admin.check_sudo_admin)):
    """Archive the database, the environment file, the xray config and the data files."""
    _guard(
        lambda: backup.create_backup(
            kind="manual",
            include_database=body.include_database,
            include_env=body.include_env,
            include_xray_config=body.include_xray_config,
            include_data=body.include_data,
            note=body.note,
        )
    )
    return _status()


@router.post(
    "/upload",
    response_model=BackupContents,
    responses={400: responses._400, 403: responses._403},
)
async def upload_backup(
    file: UploadFile = File(...), admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Take in a backup made elsewhere — a Marzban one included — and read it.

    The upload is only stored and described here. Nothing is applied until a
    restore says which parts of it to apply.
    """
    content = await file.read()
    stored = _guard(lambda: backup.store_upload(file.filename or "backup.tar.gz", content))
    return _contents(_guard(lambda: backup.inspect(stored.name)))


@router.get(
    "/{name}",
    response_model=BackupContents,
    responses={400: responses._400, 403: responses._403},
)
def inspect_backup(
    name: str = Path(pattern=NAME_PATTERN), admin: Admin = Depends(Admin.check_sudo_admin)
):
    """What one archive holds, and which parts of it can be restored here."""
    return _contents(_guard(lambda: backup.inspect(name)))


@router.get(
    "/{name}/download",
    responses={400: responses._400, 403: responses._403},
    response_class=FileResponse,
)
def download_backup(
    name: str = Path(pattern=NAME_PATTERN), admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Send the archive itself, to keep off this machine."""
    path = _guard(lambda: backup.archive_path(name))
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=os.path.basename(path),
    )


@router.post(
    "/{name}/restore",
    response_model=BackupRestoreResult,
    responses={400: responses._400, 403: responses._403},
)
def restore_backup(
    body: BackupRestore,
    name: str = Path(pattern=NAME_PATTERN),
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Put the chosen parts of an archive back.

    A backup of what is about to be replaced is taken first, so the way back
    from a restore that turns out to be the wrong one is another restore.
    """
    report = _guard(lambda: backup.restore(name, body.items))
    applied = ", ".join(report.applied)
    return BackupRestoreResult(
        applied=report.applied,
        skipped=report.skipped,
        safety_backup=report.safety_backup,
        restart_required=report.restart_required,
        detail=(
            f"Restored {applied}. Restart the panel to run on it."
            if report.restart_required
            else f"Restored {applied}."
        ),
    )


@router.delete("/{name}", response_model=BackupStatus, responses={400: responses._400, 403: responses._403})
def delete_backup(
    name: str = Path(pattern=NAME_PATTERN), admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Delete one archive."""
    _guard(lambda: backup.delete_backup(name))
    return _status()
