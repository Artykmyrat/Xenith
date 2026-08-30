"""Automatic backups, when BACKUP_INTERVAL_HOURS asks for them.

Off by default. Turned on, it makes the same archive the Backup screen makes
by hand and then prunes its own older ones down to BACKUP_KEEP — manual,
imported and pre-restore archives are never pruned, because those are the ones
somebody chose to keep.
"""

from app import logger, scheduler
from app.utils import backup
from config import BACKUP_INTERVAL_HOURS


def take_backup():
    ok, reason = backup.writable()
    if not ok:
        logger.warning(f"Skipping the automatic backup: {reason}")
        return

    try:
        made = backup.create_backup(kind="automatic", note="scheduled")
    except backup.BackupError as err:
        logger.warning(f"The automatic backup failed: {err}")
        return

    logger.info(f"Wrote {made.name} ({made.size} bytes)")
    for removed in backup.prune():
        logger.info(f"Pruned the older automatic backup {removed}")


if BACKUP_INTERVAL_HOURS > 0:
    scheduler.add_job(
        take_backup,
        "interval",
        hours=BACKUP_INTERVAL_HOURS,
        coalesce=True,
        max_instances=1,
    )
