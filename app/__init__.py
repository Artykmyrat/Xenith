import logging

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.utils.cors import cors_policy
from config import ALLOWED_ORIGINS, DEBUG, DOCS, XRAY_SUBSCRIPTION_PATH

__version__ = "0.8.4"

app = FastAPI(
    title="XenithAPI",
    description="Unified GUI Censorship Resistant Solution Powered by Xray",
    version=__version__,
    openapi_url="/openapi.json" if DOCS else None,
)

scheduler = BackgroundScheduler(
    {"apscheduler.job_defaults.max_instances": 20}, timezone="UTC"
)
logger = logging.getLogger("uvicorn.error")

_cors = cors_policy(ALLOWED_ORIGINS, debug=DEBUG)
if _cors.warning:
    logger.warning(_cors.warning)
if _cors.enabled:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors.origins,
        allow_credentials=_cors.allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
from app import dashboard, jobs, routers, telegram  # noqa
from app.routers import api_router  # noqa

app.include_router(api_router)


def use_route_names_as_operation_ids(app: FastAPI) -> None:
    for route in app.routes:
        if isinstance(route, APIRoute):
            route.operation_id = route.name


use_route_names_as_operation_ids(app)


@app.on_event("startup")
def on_startup():
    paths = [f"{r.path}/" for r in app.routes]
    paths.append("/api/")
    if f"/{XRAY_SUBSCRIPTION_PATH}/" in paths:
        raise ValueError(
            f"you can't use /{XRAY_SUBSCRIPTION_PATH}/ as subscription path it reserved for {app.title}"
        )

    # A proxy holds two descriptors per connection, so the default soft limit of
    # 1024 runs out long before anything else does. Raising soft up to hard
    # needs no privilege, so this is unconditional.
    from app.utils.limits import raise_own_limits, read_limits  # noqa: circular at import time

    report = raise_own_limits()
    nofile = next((limit for limit in read_limits() if limit.name == "nofile"), None)
    if report.raised:
        logger.info(f"Raised open file limit to {nofile.soft if nofile else '?'}")
    for problem in report.problems:
        logger.warning(f"Could not raise resource limit: {problem}")

    scheduler.start()


@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown()


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = {}
    for error in exc.errors():
        details[error["loc"][-1]] = error.get("msg")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder({"detail": details}),
    )
