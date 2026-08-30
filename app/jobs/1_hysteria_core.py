"""Starting, watching and stopping the hysteria2 daemon.

Numbered after the xray job by the convention of this directory; nothing here
depends on that, since the two daemons share no state. A hysteria that will not
start is a warning, never a failed startup: neither the panel nor a single xray
user is worse off for it.
"""

import traceback

from app import app, hysteria, logger, scheduler
from config import JOB_CORE_HEALTH_CHECK_INTERVAL


@app.on_event("startup")
def start_hysteria():
    if not hysteria.is_enabled():
        return

    logger.info("Starting Hysteria2")
    try:
        hysteria.core.start()
    except Exception:
        traceback.print_exc()

    scheduler.add_job(
        hysteria.ensure_running,
        "interval",
        seconds=JOB_CORE_HEALTH_CHECK_INTERVAL,
        coalesce=True,
        max_instances=1,
    )


@app.on_event("shutdown")
def stop_hysteria():
    if not hysteria.is_enabled():
        return

    logger.info("Stopping Hysteria2")
    hysteria.core.stop()
