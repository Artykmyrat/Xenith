from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BackupDatabase(BaseModel):
    """The live database, as far as backups are concerned."""

    kind: str
    # The file or database name; never the credentials.
    target: Optional[str] = None
    # Why it cannot be dumped, when it cannot.
    reason: Optional[str] = None


class BackupFile(BaseModel):
    name: str
    size: int
    created_at: datetime
    kind: str
    source: str
    note: str = ""


class BackupSchedule(BaseModel):
    interval_hours: int
    keep: int


class BackupStatus(BaseModel):
    enabled: bool
    writable: bool
    reason: Optional[str] = None
    paths: Dict[str, str] = {}
    database: BackupDatabase
    schedule: BackupSchedule
    total_bytes: int
    backups: List[BackupFile] = []


class BackupCreate(BaseModel):
    include_database: bool = True
    include_env: bool = True
    include_xray_config: bool = True
    include_data: bool = True
    note: str = Field("", max_length=200)


class BackupContents(BaseModel):
    """What one archive holds, and what of it this install can take."""

    name: str
    format: str
    source: str
    kind: str
    size: int
    created_at: datetime
    manifest: Optional[Dict[str, Any]] = None
    # "sqlite" or "sql" when there is a database this panel could take.
    database: Optional[str] = None
    database_member: Optional[str] = None
    database_bytes: int = 0
    env_member: Optional[str] = None
    xray_member: Optional[str] = None
    data_files: int = 0
    data_bytes: int = 0
    entries: List[str] = []
    truncated: bool = False
    restorable: List[str] = []
    warnings: List[str] = []


class BackupRestore(BaseModel):
    items: List[str] = Field(min_length=1)


class BackupRestoreResult(BaseModel):
    applied: List[str] = []
    skipped: List[str] = []
    safety_backup: Optional[str] = None
    restart_required: bool = False
    detail: str
