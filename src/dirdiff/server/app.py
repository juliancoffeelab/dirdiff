import json
import logging
import os
from http import HTTPStatus
from importlib.resources import files
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from dirdiff.db import (
    PreferencesStore,
    RepoMarkStore,
    RoomStore,
    UserProfileStore,
    open_sqlite_engine,
)
from dirdiff.engines import (
    GitAlgorithm,
    GitColorMoved,
    GitColorMovedWs,
    GitWordBoundaryMode,
    GumTreeMatcher,
)
from dirdiff.room_lord import (
    RoomLord,
)
from dirdiff.server._app.diff import ComposedDiffResponse, DiffRoutes
from dirdiff.server._app.external_agent import ExternalAgentRoutes
from dirdiff.server._app.repos import RepoRoutes
from dirdiff.server._app.review import ReviewRoutes
from dirdiff.server.base import (
    RUNTIME_CONFIG_ENV,
    ApiModel,
    ClassRoutes,
    ErrorResponse,
    RuntimeConfig,
)

__all__ = [
    "ComposedDiffResponse",
    "create_app",
    "development_uvicorn_entrypoint",
    "release_uvicorn_entrypoint",
]

LOGGER = logging.getLogger(__name__)


class UserProfileResponse(ApiModel):
    # TODO: Successful Profile routes reject missing records, so make both
    # fields required instead of admitting a partial or empty Profile.
    id: int | None

    username: str | None


class UserProfileUpdateRequest(ApiModel):
    username: str


class PreferencesResponse(ApiModel):
    user_profile_id: int

    aggressive_folds: bool

    git_color_moved: GitColorMoved

    git_color_moved_ws: GitColorMovedWs

    git_algorithm: GitAlgorithm

    difftastic_ignore_comments: bool

    gumtree_matcher: GumTreeMatcher

    git_ignore_space_change: bool

    git_word_diff: bool

    git_word_boundary: GitWordBoundaryMode

    git_word_diff_regex: str | None


class PreferencesUpdateRequest(ApiModel):
    aggressive_folds: bool

    git_color_moved: GitColorMoved

    git_color_moved_ws: GitColorMovedWs

    git_algorithm: GitAlgorithm

    difftastic_ignore_comments: bool

    gumtree_matcher: GumTreeMatcher

    git_ignore_space_change: bool

    git_word_diff: bool

    git_word_boundary: GitWordBoundaryMode

    git_word_diff_regex: Annotated[str, Field(min_length=1)] | None


class _ApplicationRoutes:
    routes = ClassRoutes()

    def __init__(
        self,
        user_profile_store: UserProfileStore,
        preferences_store: PreferencesStore,
    ) -> None:
        self.user_profile_store = user_profile_store
        self.preferences_store = preferences_store

    @routes.exception_handler(Exception)
    async def serve_unexpected_error(
        self,
        request: Request,
        error: Exception,
    ) -> JSONResponse:
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
        profile = self.user_profile_store.get(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"User profile not found: {profile_id}.",
            )
        return PreferencesResponse.model_validate(
            self.preferences_store.save(
                profile_id,
                request.aggressive_folds,
                request.git_algorithm,
                request.git_ignore_space_change,
                request.git_word_diff,
                request.git_word_boundary,
                request.git_word_diff_regex,
                request.difftastic_ignore_comments,
                request.gumtree_matcher,
                request.git_color_moved,
                request.git_color_moved_ws,
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
    presets_root: str,
    # Must be the same root room_lord was built with; source setup uses it to
    # reject persistence inside reviewed inputs.
    store_path: Path,
) -> FastAPI:
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
        store_path=store_path,
    )
    repo_routes = RepoRoutes(db)
    diff_routes = DiffRoutes(
        db,
        room_lord=room_lord,
        presets_root=presets_root,
        store_path=store_path,
    )

    app = FastAPI()
    _ApplicationRoutes.routes.register(app, application_routes)
    ReviewRoutes.routes.register(app, review_routes)
    ExternalAgentRoutes.routes.register(app, external_agent_routes)
    RepoRoutes.routes.register(app, repo_routes)
    DiffRoutes.routes.register(app, diff_routes)
    return app


def _create_runtime_app() -> FastAPI:
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
        store_path=Path(config.store_path),
    )


def development_uvicorn_entrypoint() -> FastAPI:
    app = _create_runtime_app()

    @app.get("/", response_class=HTMLResponse)
    def serve_frontend_missing() -> HTMLResponse:
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
        return FileResponse(index_path, headers={"Cache-Control": "no-cache"})

    app.mount("/assets", StaticFiles(directory=assets_path), name="assets")
    return app
