"""Construct the FastAPI application and expose its process entrypoints.

create_app composes the package route groups with concrete application-lifetime
stores and one Room service. The uvicorn entrypoints construct those dependencies
from the serialized startup contract and add the selected frontend response.

The local route group handles Profile and preference HTTP entities and the
unexpected-failure boundary. This module does not define repository, review,
external-agent, capture, or rendering behavior.
"""

import json
import logging
import os
from http import HTTPStatus
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
)
from fastapi.staticfiles import StaticFiles

from dirdiff.db import (
    PreferencesStore,
    RepoMarkStore,
    RoomStore,
    UserProfileStore,
    open_sqlite_engine,
)
from dirdiff.room_lord import (
    RoomLord,
)
from dirdiff.server.base import (
    RUNTIME_CONFIG_ENV,
    ApiModel,
    ErrorResponse,
    RuntimeConfig,
)
from dirdiff.server.diff import DiffRoutes
from dirdiff.server.external_agent import ExternalAgentRoutes
from dirdiff.server.magic import ClassRoutes
from dirdiff.server.repos import RepoRoutes
from dirdiff.server.review import ReviewRoutes

__all__ = [
    "create_app",
    "development_uvicorn_entrypoint",
    "release_uvicorn_entrypoint",
]

LOGGER = logging.getLogger(__name__)
"""Record unexpected failures at the application HTTP boundary."""


class UserProfileResponse(ApiModel):
    """Return persisted Profile identity after create, lookup, or rename.

    Profile routes validate store records through this model. Callers retain the
    id for preference and review operations and show the current username.

    The response carries no active-selection state, role, or preferences.
    """

    # TODO: Successful Profile routes reject missing records, so make both
    # fields required instead of admitting a partial or empty Profile.
    id: int | None
    """Durable Profile identity returned by successful current routes.

    The model still admits `None`, although route guards reject missing records.
    Callers must not invent an id when absence appears.
    """

    username: str | None
    """Current globally unique Profile username.

    Successful routes return a present string paired with `id`. The nullable
    model does not authorize a partial Profile or anonymous review author.
    """


class UserProfileUpdateRequest(ApiModel):
    """Supply one exact username to a Profile create or rename route.

    The called endpoint determines whether it creates or updates. Store
    validation rejects empty, edge-padded, blank, or duplicate names.

    This body does not identify the Profile being renamed; the route path does.
    """

    username: str
    """Complete username proposed for creation or rename.

    Persistence requires a unique nonblank value without edge whitespace. The
    route does not trim it or use it to select the Profile being renamed.
    """


class PreferencesResponse(ApiModel):
    """Return the complete persisted HUD preferences for one Profile.

    Preference routes validate `PreferencesRecord` through this model after read
    or write. The Profile id keeps the response bound to the addressed user.

    The server does not interpret how the HUD applies these values.
    """

    user_profile_id: int
    """Durable Profile identity associated with this preference record.

    It matches the route's addressed Profile. The HUD must not apply the value
    to another selected user merely because a username changed.
    """

    aggressive_folds: bool
    """Initial policy for folding renderer-provided unchanged intervals.

    True asks the HUD to start eligible ranges folded; false starts them open.
    The value does not collapse Files or override later reviewer actions.
    """


class PreferencesUpdateRequest(ApiModel):
    """Supply the mutable preference values to store for one Profile.

    The route path identifies the Profile; this body contains the replacement
    value written by `PreferencesStore`.

    It does not select or create a Profile and carries no defaults.
    """

    aggressive_folds: bool
    """Complete replacement for the Profile's initial folding policy.

    The route stores this exact boolean for the Profile named in its path. It
    does not create the Profile or change already mounted fold state directly.
    """


class _ApplicationRoutes:
    """Bind application-wide and Profile handlers to their exact stores.

    One instance retains Profile and preference persistence. Its declarations
    also contain the root response and the last-resort exception handler, which
    need no domain route object or additional application state.
    """

    routes = ClassRoutes()
    """Import-time declarations bound to one application route instance."""

    def __init__(
        self,
        user_profile_store: UserProfileStore,
        preferences_store: PreferencesStore,
    ) -> None:
        """Retain Profile and preference stores for the application lifetime.

        # Parameters

        - `user_profile_store`: Profile persistence used by Profile routes.
        - `preferences_store`: Preference persistence used by preference routes.
        """
        self.user_profile_store = user_profile_store
        self.preferences_store = preferences_store

    @routes.exception_handler(Exception)
    async def serve_unexpected_error(
        self,
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        """Log an unexpected HTTP failure before returning a generic response.

        # Parameters

        - `request`: Failed HTTP entity whose method and path identify damage.
        - `error`: Unexpected exception recorded with its traceback.

        The response discloses neither exception text nor traceback.

        # Usage

        FastAPI invokes this last-resort handler after typed domain and framework
        failures have taken their narrower paths.

        """
        LOGGER.error(
            "Unexpected %s %s failure",
            request.method,
            request.url.path,
            exc_info=error,
        )
        return JSONResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error."},
        )

    @routes.post(
        "/api/user-profile",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
        },
        summary="Create persisted user profile data",
    )
    def create_user_profile(
        self,
        request: UserProfileUpdateRequest,
    ) -> UserProfileResponse:
        """Create one durable Profile selected later by its exact username.

        `request` supplies a validated display name. Duplicate or otherwise
        invalid names return a client error; the endpoint selects no active
        browser identity by itself.

        # Failures

        - Raises `HTTPException` with status 400 when the username is blank,
          padded with whitespace, or already used.
        """
        try:
            return UserProfileResponse.model_validate(
                self.user_profile_store.create(request.username),
                from_attributes=True,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @routes.get(
        "/api/user-profile",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Select persisted user profile data by exact username",
    )
    def get_user_profile(self, username: str) -> UserProfileResponse:
        """Return the one existing Profile selected by its exact username.

        Username validation failures are client errors and exact absence is 404.
        A successful response exposes the durable identity and current display name
        without selecting browser state or creating preferences.

        # Parameters

        - `username`: Exact persisted display name, with no surrounding whitespace.

        # Failures

        - Raises `HTTPException` with status 400 for invalid username syntax or
          status 404 when no exact Profile exists.
        """
        try:
            profile = self.user_profile_store.get_by_username(username)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {username}.",
            )
        return UserProfileResponse.model_validate(
            profile,
            from_attributes=True,
        )

    @routes.patch(
        "/api/user-profile/{profile_id}",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Update persisted user profile data",
    )
    def update_user_profile(
        self,
        profile_id: int,
        request: UserProfileUpdateRequest,
    ) -> UserProfileResponse:
        """Rename one durable Profile without rewriting authored actions.

        # Parameters

        - `profile_id`: Existing Profile whose stable identity is preserved.
        - `request`: Validated globally unique replacement display name.

        # Failures

        - Raises `HTTPException` with status 400 for an invalid or duplicate name
          and status 404 for a missing Profile.
        """
        try:
            profile = self.user_profile_store.update_username(
                profile_id, request.username
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return UserProfileResponse.model_validate(
            profile,
            from_attributes=True,
        )

    @routes.get(
        "/api/user-profile/{profile_id}/preferences",
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Load persisted user preferences",
    )
    def serve_preferences(self, profile_id: int) -> PreferencesResponse:
        """Return one Profile's complete preferences, creating defaults if absent.

        `profile_id` must identify an existing Profile. Default creation is the
        preference store's explicit operation and does not select that Profile
        as active.

        # Failures

        - Raises `HTTPException` with status 404 when `profile_id` does not name
          an existing Profile. Persistence failures propagate.
        """
        profile = self.user_profile_store.get(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return PreferencesResponse.model_validate(
            self.preferences_store.get_or_create(profile_id),
            from_attributes=True,
        )

    @routes.patch(
        "/api/user-profile/{profile_id}/preferences",
        responses={
            HTTPStatus.NOT_FOUND: {"model": ErrorResponse},
        },
        summary="Update persisted user preferences",
    )
    def update_preferences(
        self,
        profile_id: int,
        request: PreferencesUpdateRequest,
    ) -> PreferencesResponse:
        """Replace one existing Profile's aggressive-fold preference.

        # Parameters

        - `profile_id`: Existing Profile whose preference row is updated.
        - `request`: Exact boolean value to persist and return.

        # Failures

        - Raises `HTTPException` with status 404 when `profile_id` does not name
          an existing Profile. Persistence failures propagate.
        """
        profile = self.user_profile_store.get(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return PreferencesResponse.model_validate(
            self.preferences_store.set_aggressive_folds(
                profile_id, request.aggressive_folds
            ),
            from_attributes=True,
        )


def create_app(
    db: RepoMarkStore,
    user_profile_store: UserProfileStore | None = None,
    preferences_store: PreferencesStore | None = None,
    *,
    agent_skills_root: Path,
    room_lord: RoomLord,
    presets_root: str | None = None,
) -> FastAPI:
    """Create the dirdiff FastAPI app and wire request orchestration.

    The factory constructs the route groups and binds each one to its exact
    stores and Room interface. Those groups perform HTTP validation, capture,
    Snapshot reads, and response-model validation. Persistence and rendering
    remain behind their existing domain interfaces.

    # Parameters

    - `db`: Repository registry and source of the shared SQLAlchemy engine.
    - `user_profile_store`: Profile persistence, or `None` to bind one to the
      registry engine.
    - `preferences_store`: Preference persistence, or `None` to bind one to the
      registry engine.
    - `agent_skills_root`: Exact installed instruction root exposed by agent
      onboarding.
    - `room_lord`: Application boundary for Room selection and Snapshot lookup.
    - `presets_root`: Optional catalog root; omission uses the project's test
      presets directory at request time.

    # Usage

    Construct the stores and `RoomLord` once for one database and Snapshot root,
    then keep the returned application for the server lifetime. Tests may omit
    Profile and preference stores to derive both from the registry engine.

    # Failures

    - Construction propagates dependency and route-registration failures. HTTP
      operation failures are handled by the installed application handlers.
    """
    if user_profile_store is None:
        user_profile_store = UserProfileStore(db.engine)
    if preferences_store is None:
        preferences_store = PreferencesStore(db.engine)

    application_routes = _ApplicationRoutes(
        user_profile_store,
        preferences_store,
    )
    review_routes = ReviewRoutes(room_lord)
    external_agent_routes = ExternalAgentRoutes(
        db,
        user_profile_store,
        agent_skills_root=agent_skills_root,
        room_lord=room_lord,
        presets_root=presets_root,
    )
    repo_routes = RepoRoutes(db)
    diff_routes = DiffRoutes(
        db,
        room_lord=room_lord,
        presets_root=presets_root,
    )

    app = FastAPI()
    _ApplicationRoutes.routes.register(app, application_routes)
    ReviewRoutes.routes.register(app, review_routes)
    ExternalAgentRoutes.routes.register(app, external_agent_routes)
    RepoRoutes.routes.register(app, repo_routes)
    DiffRoutes.routes.register(app, diff_routes)
    return app


def _create_runtime_app() -> FastAPI:
    """Construct one API application from serialized CLI configuration.

    The development and release uvicorn factories share this application-lifetime
    operation. It reads the process handoff once, opens one SQLite engine, builds
    the stores and Room service, and returns an app with API and documentation
    routes but no frontend route.

    # Usage

    The two uvicorn factories call this after the CLI writes
    `RUNTIME_CONFIG_ENV`. Application code with constructed dependencies should
    call `create_app` directly.

    # Failures

    - Asserts when runtime configuration is absent or the configured database
      has no active repository mark.
    - Propagates invalid JSON, configuration, database, and schema failures.
    """
    payload = os.environ.get(RUNTIME_CONFIG_ENV)
    assert payload is not None, "dirdiff runtime config missing"
    config = RuntimeConfig(**json.loads(payload))
    engine = open_sqlite_engine(
        Path(config.db_path), Path(config.migration_config_path)
    )
    repo_store = RepoMarkStore(engine)
    room_lord = RoomLord(RoomStore(engine), Path(config.store_path))
    user_profile_store = UserProfileStore(engine)
    preferences_store = PreferencesStore(engine)
    marks = repo_store.list()
    assert marks != [], "dirdiff runtime config has no marked repos"
    return create_app(
        repo_store,
        user_profile_store,
        preferences_store,
        agent_skills_root=Path(config.agent_skills_path),
        room_lord=room_lord,
        presets_root=config.presets_root,
    )


def development_uvicorn_entrypoint() -> FastAPI:
    """Compose the reloadable API with its missing-Vite diagnostic page.

    Editable CLI startup uses this factory whether Vite starts successfully or
    the user selects backend-only development. Vite serves the real HUD on its
    own port; the backend root only explains that fixed development contract.
    """

    app = _create_runtime_app()

    @app.get("/", response_class=HTMLResponse)
    def serve_frontend_missing() -> HTMLResponse:
        """Explain that the development API has no bundled HUD to serve."""

        return HTMLResponse(
            """
            <!doctype html>
            <html lang="en">
              <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="width=device-width, initial-scale=1" />
                <title>dirdiff frontend unavailable</title>
                <style>
                  body {
                    margin: 0;
                    background: #fbfaf7;
                    color: #24231f;
                    font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                  }
                  main {
                    max-width: 640px;
                    margin: 72px auto;
                    padding: 0 24px;
                  }
                  h1 {
                    margin: 0 0 12px;
                    font-size: 28px;
                  }
                  p {
                    color: #625f58;
                    line-height: 1.45;
                  }
                  code {
                    color: #24231f;
                    font-weight: 700;
                  }
                </style>
              </head>
              <body>
                <main>
                  <h1>Oops, the Vite frontend is not running.</h1>
                  <p>
                    dirdiff's API server is up, but the browser UI is served by
                    Vite during local runs. Start dirdiff without
                    <code>--no-frontend-dev</code>, or check the terminal for why
                    Vite refused to start.
                  </p>
                </main>
              </body>
            </html>
            """,
            status_code=503,
        )

    return app


def release_uvicorn_entrypoint() -> FastAPI:
    """Compose the API with the HUD installed inside the dirdiff package.

    Standard installations use this factory without Vite or reload. Construction
    refuses a malformed wheel before uvicorn accepts traffic. The root returns
    the compiled entry page, `/assets` returns Vite output, and all other browser
    paths keep FastAPI's normal 404 response.
    """

    app = _create_runtime_app()
    frontend_path = Path(str(files("dirdiff").joinpath("frontend")))
    index_path = frontend_path / "index.html"
    assets_path = frontend_path / "assets"
    assert index_path.is_file(), f"bundled HUD entry is missing: {index_path}"
    assert assets_path.is_dir(), (
        f"bundled HUD assets are missing: {assets_path}"
    )

    @app.get("/")
    def serve_release_frontend() -> FileResponse:
        """Return the installed HUD entry page for every root query string."""

        return FileResponse(index_path)

    app.mount("/assets", StaticFiles(directory=assets_path), name="assets")
    return app
