"""The startup handler, and the assumptions it makes about `app.routes`.

Two pieces of wiring read `app.routes` expecting the individual routes to be
sitting there: `on_startup` refuses a subscription path that would shadow one,
and `use_route_names_as_operation_ids` names every operation after its handler.
A framework upgrade can quietly take that away — FastAPI 0.137 folds included
routers into a single object, which leaves the first raising AttributeError and
the second silently naming nothing, changing every operation id in the schema.

The rest of the suite cannot catch either. Its client fixture deliberately
skips the lifespan, so `on_startup` never runs, and nothing else asserts that
operation ids were assigned. Hence these, which call the handler directly with
the scheduler and the rlimit work stubbed out.
"""

import pytest
from fastapi.routing import APIRoute

import app as app_module
from app import app as fastapi_app
from app.utils import limits


@pytest.fixture
def startup(monkeypatch):
    """`on_startup` with its side effects removed.

    Only the route bookkeeping is under test here; starting the scheduler and
    moving this process's rlimits are what the fixture takes away.
    """
    started = []
    monkeypatch.setattr(app_module.scheduler, "start", lambda: started.append(True))
    monkeypatch.setattr(limits, "raise_own_limits", lambda: limits.RaiseReport())
    monkeypatch.setattr(limits, "read_limits", lambda: [])
    return started


def api_routes():
    return [route for route in fastapi_app.routes if isinstance(route, APIRoute)]


class TestRoutesAreReachable:
    """`app.routes` has to hold the routes themselves, not a wrapper around them."""

    def test_the_api_routes_are_listed_individually(self):
        assert len(api_routes()) > 50

    def test_every_route_exposes_a_path(self):
        assert all(hasattr(route, "path") for route in fastapi_app.routes)

    def test_every_route_is_named_after_its_handler(self):
        routes = api_routes()
        unnamed = [route.path for route in routes if not route.operation_id]

        assert routes and unnamed == []

    def test_operation_ids_match_the_endpoint_names(self):
        routes = api_routes()
        mismatched = [route.path for route in routes if route.operation_id != route.name]

        assert routes and mismatched == []


class TestSubscriptionPathGuard:
    """The panel refuses to start on a subscription path that shadows the API."""

    def test_the_configured_path_starts_the_scheduler(self, startup):
        app_module.on_startup()

        assert startup == [True]

    def test_a_path_that_shadows_an_endpoint_is_refused(self, startup, monkeypatch):
        monkeypatch.setattr(app_module, "XRAY_SUBSCRIPTION_PATH", "api/system")

        with pytest.raises(ValueError):
            app_module.on_startup()

        assert startup == []

    def test_the_reserved_api_prefix_is_refused(self, startup, monkeypatch):
        monkeypatch.setattr(app_module, "XRAY_SUBSCRIPTION_PATH", "api")

        with pytest.raises(ValueError):
            app_module.on_startup()

        assert startup == []
