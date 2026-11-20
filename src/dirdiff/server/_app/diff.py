import logging
from http import HTTPStatus
from pathlib import Path
from typing import (
    Annotated,
    Literal,
    Optional,
)
from uuid import UUID

from fastapi import HTTPException, Query
from fastapi.responses import (
    Response,
)
from pydantic import (
    Field,
    Json,
    model_validator,
)

from dirdiff.change_source import (
    LazyReason,
    ManifestPath,
    PresetCatalog,
    PresetCatalogDir,
    build_lazy_info_for_paths,
    build_repo_manifest_for_paths,
    display_name_for_repo_paths,
    file_kind_for_change_type,
)
from dirdiff.db import (
    RepoMarkStore,
)
from dirdiff.engines import (
    DirdiffError,
    EngineConfig,
    InlineTokenStatus,
    PairedMove,
    UnpairedMove,
    engine,
)
from dirdiff.formats import (
    BayContext,
    ComposeContext,
    Composer,
    ImageBay,
)
from dirdiff.rendering import (
    SyntaxClass,
)
from dirdiff.review import (
    FilePair,
)
from dirdiff.room_lord import (
    CapturedFileSide,
    FileMeta,
    Room,
    RoomLord,
)
from dirdiff.server.base import (
    ApiModel,
    ClassRoutes,
    ErrorResponse,
    ManifestSelection,
    PreparedCapture,
    PresetManifestSelection,
    preset_catalog_dirs,
)
from dirdiff.util import Nothing, Option, Some, maybe

__all__ = ["ComposedDiffResponse", "DiffRoutes"]

LOGGER = logging.getLogger(__name__)

ChangeType = Literal["modify", "add", "delete", "rename", "copy"]


class PresetGroupResponse(ApiModel):
    id: str
    display_name: str


class PresetCatalogResponse(ApiModel):
    id: str
    name: str
    default_preset: str
    groups: list[PresetGroupResponse]


class DecoratedPartResponse(ApiModel):
    text: str
    syntax_classes: list[SyntaxClass]
    # Move spans use one-based source lines and zero-based Unicode columns.
    move: PairedMove | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    diff_status: InlineTokenStatus
    is_whitespace: bool
    # Implies `is_whitespace`; only the run before the first content counts.
    is_leading_whitespace: bool


class FoldHintResponse(ApiModel):
    # Zero-based half-open interval of row indexes within one bay's `rows`.
    start_row: int
    end_row: int
    kind: Literal[
        "function_like",
        "class_like",
        "container",
        "section",
        "top_level",
    ]
    label: str


class DiffRowResponse(ApiModel):
    move: UnpairedMove | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    status: Literal["equal", "replace", "insert", "delete", "move"]
    # One-based source line numbers. None for an absent side, and for Git word
    # fragments made only of whitespace; Git word fragments may repeat a number.
    left_no: int | None
    right_no: int | None
    left_text: str | None
    right_text: str | None
    left_parts: list[DecoratedPartResponse]
    right_parts: list[DecoratedPartResponse]
    # Zero-based bay-local index (not File-wide) of the hunk starting on this
    # row, else None; assigned before frontend folding or virtualization.
    hunk_index: int | None


class DiffSummaryResponse(ApiModel):
    # Counts cover text bays only. `*_exists` is True for an empty File, so
    # side absence must not be inferred from the counts.
    changed_lines: int
    modified_lines: int
    added_lines: int
    removed_lines: int
    moved_lines: int = 0
    left_exists: bool
    right_exists: bool


class RepoDiffSummaryResponse(ApiModel):
    changed_files: int
    added_files: int
    removed_files: int
    updated_files: int
    added_lines: Optional[int]
    removed_lines: Optional[int]
    skipped_files: int
    # The cell counts are not populated by manifests; None means unavailable,
    # not zero.
    changed_cells: int | None = None
    added_cells: int | None = None
    removed_cells: int | None = None
    modified_cells: int | None = None

    @model_validator(mode="after")
    def validate_line_count_presence(self) -> RepoDiffSummaryResponse:
        if (self.added_lines is None) != (self.removed_lines is None):
            raise ValueError(
                "added_lines and removed_lines must have equal presence"
            )
        return self


class TrackedFileKindResponse(ApiModel):
    type: Literal["tracked"]
    status: Literal["modified", "added", "deleted", "renamed", "copied"]


class UntrackedFileKindResponse(ApiModel):
    type: Literal["untracked"]


FileKindResponse = TrackedFileKindResponse | UntrackedFileKindResponse


class BayWarningResponse(ApiModel):
    type: str = Field(min_length=1)
    message: str


class BayStatsResponse(ApiModel):
    changed_lines: int
    modified_lines: int
    added_lines: int
    removed_lines: int
    moved_lines: int = 0


class ChangeStatusResponse(ApiModel):
    kind: Literal["added", "removed", "changed", "unchanged"]


class MovedChangeStatusResponse(ApiModel):
    kind: Literal["moved"]
    from_heading: str | None
    to_heading: str | None


class TextKindResponse(ApiModel):
    kind: Literal["text"]
    left_label: str
    right_label: str
    rows: list[DiffRowResponse]
    fold_hints: list[FoldHintResponse] = Field(default_factory=list)
    stats: BayStatsResponse
    # True for Git word mode: each row is one tool-printed line and the inline
    # grid interleaves an equal row's sides back into it. False: aligned
    # physical lines, one per side. Required so the HUD never guesses.
    interleave_inline_rows: bool


class MediaRefResponse(ApiModel):
    media_type: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    # Lowercase hex SHA-256 of the exact media bytes.
    digest: str = Field(min_length=1)


class ImageKindResponse(ApiModel):
    kind: Literal["image"]
    # None when the side is absent or a notebook output has no PNG there; not
    # an empty or failed image.
    left: MediaRefResponse | None = None
    right: MediaRefResponse | None = None


BayKindResponse = Annotated[
    TextKindResponse | ImageKindResponse,
    Field(discriminator="kind"),
]


class BayResponse(ApiModel):
    # Stable sub-File coordinate shared with line pins and Thread targets;
    # clients return it unchanged. `label` is presentation and may repeat.
    bay_key: str = Field(min_length=1)
    label: str
    detail: str | None = None
    collapsible: bool
    default_expanded: bool
    # Decided by the format builder: row equality cannot recover moves or
    # changes beyond the rendered text, so the HUD must not re-derive it.
    change: Annotated[
        ChangeStatusResponse | MovedChangeStatusResponse,
        Field(discriminator="kind"),
    ]
    warnings: list[BayWarningResponse]
    kind_data: BayKindResponse


class FrameResponse(ApiModel):
    frame_key: str = Field(min_length=1)
    heading: str | None = None
    bays: list[BayResponse]


class ComposedDiffResponse(ApiModel):
    display_name: str
    left_label: str
    right_label: str
    left_path: str | None = None
    right_path: str | None = None
    file_kind: FileKindResponse
    summary: DiffSummaryResponse
    default_expanded: bool = True
    frames: list[FrameResponse]


class RepoFileEntryResponse(ApiModel):
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    # TODO: Lazy-info contains deferred Files only, so require the concrete
    # reason that `build_lazy_info_for_paths` always supplies.
    lazy: LazyReason | None = None


class RepoManifestFileNodeResponse(ApiModel):
    type: Literal["file"]
    name: str
    entry: RepoFileEntryResponse


class RepoManifestDirectoryNodeResponse(ApiModel):
    type: Literal["directory"]
    name: str
    path: str
    # Source-supplied order; the HUD must not sort it. `path` is the identity,
    # `name` may be a compacted single-child chain containing separators.
    entries: list[RepoManifestTreeEntryResponse]


RepoManifestTreeEntryResponse = (
    RepoManifestFileNodeResponse | RepoManifestDirectoryNodeResponse
)


class LazyInfoFileResponse(ApiModel):
    # This response must contain enough data for the frontend to construct a
    # lazy placeholder FileEntry without copying fields from /api/manifest.
    file_kind: FileKindResponse
    left_path: str | None = None
    right_path: str | None = None
    display_name: str
    # None means unknown until the File is rendered, not zero.
    changed_lines: int | None = None
    added_lines: int | None = None
    removed_lines: int | None = None
    lazy: LazyReason | None = None


class RepoManifestResponse(ApiModel):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    display_name: str
    left_label: str
    right_label: str
    summary: RepoDiffSummaryResponse
    tree: list[RepoManifestTreeEntryResponse]


class LazyInfoResponse(ApiModel):
    files: list[LazyInfoFileResponse]


class ManifestQuery(ApiModel):
    selection: Json[ManifestSelection]


def captured_manifest_paths(
    room: Room,
    snapshot_id: UUID,
    *,
    preset: bool,
) -> tuple[ManifestPath, ...]:
    paths: list[ManifestPath] = []
    for left, right, metadata in room.manifested(snapshot_id):
        left_path = left.map(Path.as_posix)
        right_path = right.map(Path.as_posix)
        display_name = (
            right_path.value
            if preset and Some.present(right_path)
            else display_name_for_repo_paths(left_path, right_path)
        )
        paths.append(
            ManifestPath(
                left_path=left_path,
                right_path=right_path,
                display_name=display_name,
                change_type=metadata["change_type"],
                lazy_reason_override=metadata["lazy_reason_override"],
                untracked=metadata["untracked"],
            )
        )
    return tuple(
        sorted(paths, key=lambda path: (path.display_name, path.change_type))
    )


class FileDiffQuery(ApiModel):
    snapshot_id: str
    engine: Json[EngineConfig]
    left_path: str | None = None
    right_path: str | None = None


class DiffRoutes:
    routes = ClassRoutes()

    def __init__(
        self,
        db: RepoMarkStore,
        *,
        room_lord: RoomLord,
        presets_root: str,
        store_path: Path,
    ) -> None:
        self.db = db
        self.room_lord = room_lord
        self.presets_root = presets_root
        self.store_path = store_path

    def preset_catalog_response(
        self,
        catalog: PresetCatalogDir,
    ) -> PresetCatalogResponse:
        preset_catalog = PresetCatalog(catalog.root)
        return PresetCatalogResponse.model_validate(
            {
                "id": catalog.catalog_id,
                "name": catalog.name,
                "default_preset": preset_catalog.default_preset_name(),
                "groups": preset_catalog.list_preset_groups(),
            }
        )

    def render_loaded_snapshot_file(
        self,
        *,
        room: Room,
        snapshot_id: UUID,
        engine_config: EngineConfig,
        pair: FilePair,
        left_file: Option[CapturedFileSide],
        right_file: Option[CapturedFileSide],
        file_meta: FileMeta,
    ) -> ComposedDiffResponse:
        if file_meta["capture_error"] is not None:
            raise DirdiffError(file_meta["capture_error"])
        snapshot_meta = room.meta(snapshot_id)
        left_bytes = left_file.map(lambda side: side.path.read_bytes())
        right_bytes = right_file.map(lambda side: side.path.read_bytes())
        left_link = left_file.and_then(
            lambda side: (
                Some(side.link) if side.kind == "symlink" else Nothing()
            )
        )
        right_link = right_file.and_then(
            lambda side: (
                Some(side.link) if side.kind == "symlink" else Nothing()
            )
        )
        context = ComposeContext.build(
            left_path=pair.left_path,
            right_path=pair.right_path,
            left_label=snapshot_meta["left_label"],
            right_label=snapshot_meta["right_label"],
            left_link=left_link,
            right_link=right_link,
            renderer=engine(engine_config),
        )
        composed = Composer().compose(left_bytes, right_bytes, context)
        # TODO: the frontend should probably take the display name and file kind
        # from the manifest, which already emits one per File. Until then the
        # HTTP boundary attaches both here: the two envelope fields composition
        # deliberately does not produce.
        file_kind: Literal["tracked", "untracked"] = (
            "untracked" if file_meta["untracked"] else "tracked"
        )
        display_name = (
            pair.right_path.value
            if snapshot_meta["tab"] == "preset"
            and Some.present(pair.right_path)
            else display_name_for_repo_paths(pair.left_path, pair.right_path)
        )
        return ComposedDiffResponse.model_validate(
            {
                **composed,
                "display_name": display_name,
                "file_kind": file_kind_for_change_type(
                    file_meta["change_type"],
                    file_kind=file_kind,
                ),
            }
        )

    @routes.get(
        "/api/presets",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load grouped preset metadata",
    )
    def serve_presets(self) -> list[PresetCatalogResponse]:
        # The root is rescanned on every call so hot-added catalogs appear.
        try:
            return [
                self.preset_catalog_response(catalog)
                for catalog in preset_catalog_dirs(self.presets_root)
            ]
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Preset catalog request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

    @routes.get(
        "/api/manifest",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Show the repository state selected by a Tab",
    )
    def serve_manifest(
        self,
        query: Annotated[ManifestQuery, Query()],
    ) -> RepoManifestResponse:
        # `selection` is one JSON-encoded object, not separate query fields.
        try:
            prepared: PreparedCapture = query.selection.prepare(
                self.db,
                self.presets_root,
                store_path=self.store_path,
            )
            room, snapshot_id = self.room_lord.corresponding_room(
                mark_id=prepared.mark_id,
                selection=prepared.correspondence,
                source=prepared.source,
                left_label=prepared.left_label,
                right_label=prepared.right_label,
            )
            snapshot_meta = room.meta(snapshot_id)
            manifest_paths = captured_manifest_paths(
                room, snapshot_id, preset=snapshot_meta["tab"] == "preset"
            )
            manifest = build_repo_manifest_for_paths(
                left_label=snapshot_meta["left_label"],
                right_label=snapshot_meta["right_label"],
                paths=manifest_paths,
                added_lines=snapshot_meta["added_lines"],
                removed_lines=snapshot_meta["removed_lines"],
            )
            if isinstance(query.selection, PresetManifestSelection):
                manifest["display_name"] = query.selection.subset
            payload = {"snapshot_id": snapshot_id.hex, **manifest}
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Manifest request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return RepoManifestResponse.model_validate(payload)

    @routes.get(
        "/api/lazy-info",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load repository lazy file metadata",
    )
    def serve_lazy_info(
        self,
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
    ) -> LazyInfoResponse:
        try:
            try:
                snapshot_key = UUID(hex=snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {snapshot_id}"
                ) from exc
            room = self.room_lord.find_room(snapshot_key)
            snapshot_meta = room.meta(snapshot_key)
            lazy_paths = captured_manifest_paths(
                room, snapshot_key, preset=snapshot_meta["tab"] == "preset"
            )
            payload = build_lazy_info_for_paths(paths=lazy_paths)
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("Lazy info request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return LazyInfoResponse.model_validate(payload)

    @routes.get(
        "/api/file-diff",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Load a single file diff",
    )
    def serve_file_diff(
        self,
        params: Annotated[FileDiffQuery, Query()],
    ) -> ComposedDiffResponse:
        try:
            try:
                snapshot_key = UUID(hex=params.snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {params.snapshot_id}"
                ) from exc
            try:
                pair = FilePair(
                    maybe(params.left_path), maybe(params.right_path)
                )
            except ValueError as exc:
                raise DirdiffError(str(exc)) from exc
            room = self.room_lord.find_room(snapshot_key)
            left = pair.left_path.map(Path)
            right = pair.right_path.map(Path)
            left_file, right_file, file_meta = room.get(
                snapshot_key, left, right
            )
            payload = self.render_loaded_snapshot_file(
                room=room,
                snapshot_id=snapshot_key,
                engine_config=params.engine,
                pair=pair,
                left_file=left_file,
                right_file=right_file,
                file_meta=file_meta,
            )
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("File diff request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return payload

    @routes.get(
        "/api/file-media",
        responses={
            HTTPStatus.BAD_REQUEST: {"model": ErrorResponse},
            HTTPStatus.INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        summary="Serve one composed image-bay side",
        response_class=Response,
    )
    def serve_file_media(
        self,
        snapshot_id: str = Query(
            description="Opaque Snapshot id returned by /api/manifest.",
        ),
        bay_key: str = Query(
            description="Exact File-local image bay key from /api/file-diff.",
        ),
        side: Literal["left", "right"] = Query(
            description="Which composed side of the image bay to serve.",
        ),
        left_path: str | None = Query(
            default=None, description="Repo-relative path on the left side."
        ),
        right_path: str | None = Query(
            default=None, description="Repo-relative path on the right side."
        ),
    ) -> Response:
        try:
            try:
                snapshot_key = UUID(hex=snapshot_id)
            except ValueError as exc:
                raise DirdiffError(
                    f"Unknown snapshot id: {snapshot_id}"
                ) from exc
            try:
                pair = FilePair(maybe(left_path), maybe(right_path))
            except ValueError as exc:
                raise DirdiffError(str(exc)) from exc
            room = self.room_lord.find_room(snapshot_key)
            left_file, right_file, file_meta = room.get(
                snapshot_key,
                pair.left_path.map(Path),
                pair.right_path.map(Path),
            )
            if file_meta["capture_error"] is not None:
                raise DirdiffError(file_meta["capture_error"])
            snapshot_meta = room.meta(snapshot_key)
            left_link = left_file.and_then(
                lambda side: (
                    Some(side.link) if side.kind == "symlink" else Nothing()
                )
            )
            right_link = right_file.and_then(
                lambda side: (
                    Some(side.link) if side.kind == "symlink" else Nothing()
                )
            )
            # `bays()` runs no diff engine, so serving a picture never renders a
            # diff.
            for bay in Composer().bays(
                left_file.map(lambda side: side.path.read_bytes()),
                right_file.map(lambda side: side.path.read_bytes()),
                BayContext(
                    left_path=pair.left_path,
                    right_path=pair.right_path,
                    left_label=snapshot_meta["left_label"],
                    right_label=snapshot_meta["right_label"],
                    left_link=left_link,
                    right_link=right_link,
                ),
            ):
                if bay.bay_key != bay_key:
                    continue
                if not isinstance(bay, ImageBay):
                    raise DirdiffError(
                        f"Bay {bay_key!r} does not carry media content."
                    )
                media_bay = bay
                break
            else:
                raise DirdiffError(
                    f"The selected file has no bay named {bay_key!r}."
                )
            media_side = media_bay.left if side == "left" else media_bay.right
            if not Some.present(media_side):
                raise DirdiffError(
                    f"Bay {bay_key!r} has no image on the {side} side."
                )
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            LOGGER.exception("File media request crashed: %s", exc)
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="Internal server error.",
            ) from exc

        return Response(
            content=media_side.value.data,
            media_type=media_side.value.media_type,
            # Snapshots are immutable and ids never reused, so this address
            # can never change content.
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )
