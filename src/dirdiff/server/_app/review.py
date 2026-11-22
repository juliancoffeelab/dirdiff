from datetime import datetime
from http import HTTPStatus
from typing import (
    Annotated,
    Literal,
    Self,
)
from uuid import UUID, uuid4

from fastapi import HTTPException, Query, Request
from fastapi.responses import (
    JSONResponse,
    Response,
)
from pydantic import (
    Field,
    model_validator,
)
from starlette.concurrency import run_in_threadpool

from dirdiff.engines import (
    DirdiffError,
)
from dirdiff.review import (
    AddComment,
    BatchCreate,
    ChangeThreadState,
    CreateReviewThread,
    CreateThread,
    DeleteComment,
    EditComment,
    FilePair,
    LineRange,
    ProfileAuthor,
    ReviewError,
    ReviewErrorCode,
    TextTarget,
)
from dirdiff.room_lord import (
    ATTACHMENT_SIZE_MAX,
    RoomLord,
)
from dirdiff.server.base import (
    ApiModel,
    ClassRoutes,
    ErrorResponse,
    Responses,
    ReviewExcerptResponse,
)
from dirdiff.util import Some, maybe

__all__ = ["ReviewRoutes"]


class ReviewFilePairModel(ApiModel):
    left_path: str | None
    right_path: str | None

    @model_validator(mode="after")
    def validate_presence(self) -> Self:
        FilePair(maybe(self.left_path), maybe(self.right_path))
        return self


class ReviewLineRange(ApiModel):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        LineRange(self.start_line, self.end_line)
        return self


class ReviewTextBayModel(ApiModel):
    bay_key: str = Field(min_length=1)


class TextReviewTarget(ApiModel):
    kind: Literal["text"]
    file: ReviewFilePairModel
    bay: ReviewTextBayModel
    side: Literal["left", "right"]
    range: ReviewLineRange

    @model_validator(mode="after")
    def validate_selected_side(self) -> Self:
        if self.side == "left" and self.file.left_path is None:
            raise ValueError("The selected left side is absent.")
        if self.side == "right" and self.file.right_path is None:
            raise ValueError("The selected right side is absent.")
        return self


class ReviewScopeTarget(ApiModel):
    kind: Literal["review"]


ReviewTargetModel = Annotated[
    TextReviewTarget | ReviewScopeTarget,
    Field(discriminator="kind"),
]


class NewCodeCommentRequest(ApiModel):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)
    target: ReviewTargetModel
    body: str = Field(min_length=1)


class ReplyCommentRequest(ApiModel):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)
    body: str = Field(min_length=1)
    attention: Literal["inert", "alert"]


PostCommentRequest = NewCodeCommentRequest | ReplyCommentRequest


class EditReviewCommentRequest(ApiModel):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)
    body: str = Field(min_length=1)


class DeleteReviewCommentRequest(ApiModel):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)


class ChangeReviewThreadStateRequest(ApiModel):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)
    body: str | None = Field(default=None, min_length=1)


class DeleteReviewThreadRequest(ApiModel):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_id: int = Field(gt=0)


class ReviewAuthorResponse(ApiModel):
    profile_id: int = Field(gt=0)
    display_name: str = Field(min_length=1)
    is_agent: bool
    model: str | None
    harness: str | None

    @model_validator(mode="after")
    def validate_human(self) -> Self:
        if not self.is_agent and (
            self.model is not None or self.harness is not None
        ):
            raise ValueError("Human authors have no model or harness.")
        return self


class ReviewCommentResponse(ApiModel):
    comment_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sequence: int = Field(ge=0)
    author: ReviewAuthorResponse
    revision: int = Field(ge=0)
    body: str | None
    deleted: bool
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_tombstone(self) -> Self:
        if self.deleted != (self.body is None):
            raise ValueError("Deleted Comments must be body-less tombstones.")
        return self


class ThreadRegionKeptPlacementResponse(ApiModel):
    kind: Literal["region-kept"]
    range: ReviewLineRange


class ThreadRegionChangedPlacementResponse(ApiModel):
    kind: Literal["region-changed"]
    range: ReviewLineRange


class ThreadRegionLostPlacementResponse(ApiModel):
    kind: Literal["region-lost"]


class ThreadBayLostPlacementResponse(ApiModel):
    kind: Literal["bay-lost"]
    bay: ReviewTextBayModel


class ThreadSideLostPlacementResponse(ApiModel):
    kind: Literal["side-lost"]


class ThreadFileAbsentPlacementResponse(ApiModel):
    kind: Literal["file-absent"]


class ThreadFileUnreadablePlacementResponse(ApiModel):
    kind: Literal["file-unreadable"]


class ThreadWholeFilePlacementResponse(ApiModel):
    kind: Literal["whole-file"]


class ThreadReviewScopePlacementResponse(ApiModel):
    kind: Literal["review-scope"]


ThreadPlacementResponse = Annotated[
    ThreadRegionKeptPlacementResponse
    | ThreadRegionChangedPlacementResponse
    | ThreadRegionLostPlacementResponse
    | ThreadBayLostPlacementResponse
    | ThreadSideLostPlacementResponse
    | ThreadFileAbsentPlacementResponse
    | ThreadFileUnreadablePlacementResponse
    | ThreadWholeFilePlacementResponse
    | ThreadReviewScopePlacementResponse,
    Field(discriminator="kind"),
]


class ThreadDisplayedLocationSourceResponse(ApiModel):
    kind: Literal["displayed-snapshot"]


class ThreadOriginLocationSourceResponse(ApiModel):
    kind: Literal["origin-snapshot"]
    file_state: Literal["unchanged", "changed", "missing", "unreadable"]


ThreadLocationSourceResponse = Annotated[
    ThreadDisplayedLocationSourceResponse | ThreadOriginLocationSourceResponse,
    Field(discriminator="kind"),
]


class TextReviewOriginResponse(TextReviewTarget):
    excerpt: ReviewExcerptResponse


class FileStartReviewOriginResponse(ApiModel):
    kind: Literal["file-start"]
    file: ReviewFilePairModel
    side: Literal["left", "right"]


class ReviewScopeOriginResponse(ApiModel):
    kind: Literal["review"]


ReviewOriginResponse = Annotated[
    TextReviewOriginResponse
    | FileStartReviewOriginResponse
    | ReviewScopeOriginResponse,
    Field(discriminator="kind"),
]


class ReviewThreadResponse(ApiModel):
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: datetime
    state: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]
    discussion_revision: int = Field(ge=0)
    origin_target: ReviewOriginResponse
    placement: ThreadPlacementResponse
    location_source: ThreadLocationSourceResponse
    comments: list[ReviewCommentResponse] = Field(min_length=1)


class ReviewThreadUpdateResponse(ApiModel):
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    state: Literal["open", "resolved", "deleted"]
    attention: Literal["author", "reviewer", "both", "none"]
    discussion_revision: int = Field(ge=0)
    comment: ReviewCommentResponse | None


class ReviewThreadPage(ApiModel):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    through_activity_id: int = Field(ge=0)
    threads: list[ReviewThreadResponse]
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    total_threads: int = Field(ge=0)
    has_more: bool


class ReviewAttachmentResponse(ApiModel):
    url: str = Field(pattern=r"^attachment:[0-9a-f]{64}$")


class ReviewErrorResponse(ApiModel):
    code: ReviewErrorCode
    message: str = Field(min_length=1)


class _ReviewHttpException(Exception):
    def __init__(self, status: HTTPStatus, error: ReviewError) -> None:
        super().__init__(str(error))
        self.status = status
        self.response = ReviewErrorResponse(
            code=error.code,
            message=str(error),
        )


_REVIEW_ERROR_RESPONSES: Responses = {
    HTTPStatus.BAD_REQUEST: {"model": ReviewErrorResponse},
    HTTPStatus.NOT_FOUND: {"model": ReviewErrorResponse},
    HTTPStatus.FORBIDDEN: {"model": ReviewErrorResponse},
    HTTPStatus.CONFLICT: {"model": ReviewErrorResponse},
}

_ATTACHMENT_UPLOAD_RESPONSES: Responses = {
    HTTPStatus.BAD_REQUEST: {"model": ReviewErrorResponse},
    HTTPStatus.NOT_FOUND: {"model": ReviewErrorResponse},
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: {"model": ReviewErrorResponse},
}


class ReviewRoutes:
    routes = ClassRoutes()

    def __init__(self, room_lord: RoomLord) -> None:
        self.room_lord = room_lord

    def review_http_exception(self, error: ReviewError) -> _ReviewHttpException:
        if error.code in {
            "profile_not_found",
            "thread_not_found",
            "comment_not_found",
        }:
            status = HTTPStatus.NOT_FOUND
        elif error.code == "forbidden":
            status = HTTPStatus.FORBIDDEN
        elif error.code in {
            "revision_conflict",
            "state_conflict",
        }:
            status = HTTPStatus.CONFLICT
        else:
            assert error.code == "invalid_target"
            status = HTTPStatus.BAD_REQUEST
        return _ReviewHttpException(status, error)

    @routes.exception_handler(_ReviewHttpException)
    async def serve_review_error(
        self,
        request: Request,
        error: _ReviewHttpException,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status,
            content=error.response.model_dump(mode="json"),
        )

    @routes.get(
        "/api/review/threads",
        response_model=ReviewThreadPage,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Read one page of review Threads",
    )
    def serve_review(
        self,
        snapshot_id: UUID = Query(
            description="Displayed Snapshot used to select one Room and placements."
        ),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=100),
        through_activity_id: int | None = Query(default=None, ge=0),
    ) -> ReviewThreadPage:
        try:
            room = self.room_lord.find_room(snapshot_id)
            if page == 1:
                if through_activity_id is not None:
                    raise ReviewError(
                        "invalid_target",
                        "First review page chooses its activity pivot.",
                    )
            elif through_activity_id is None:
                raise ReviewError(
                    "invalid_target",
                    "Later review pages require the first page activity pivot.",
                )
            threads, total, through_activity_id = room.threads(
                snapshot_id,
                page=page,
                limit=limit,
                state="all",
                scope="room",
                through_activity_id=through_activity_id,
            )
            discussions = [thread.discussion() for thread in threads]
            return ReviewThreadPage(
                snapshot_id=snapshot_id.hex,
                through_activity_id=through_activity_id,
                threads=[
                    ReviewThreadResponse.model_validate(discussion)
                    for discussion in discussions
                ],
                page=page,
                limit=limit,
                total_threads=total,
                has_more=page * limit < total,
            )
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    def review_target(self, target: TextReviewTarget) -> TextTarget:
        file = FilePair(
            maybe(target.file.left_path),
            maybe(target.file.right_path),
        )
        return TextTarget(
            file,
            target.bay.bay_key,
            target.side,
            LineRange(
                target.range.start_line,
                target.range.end_line,
            ),
        )

    @routes.post(
        "/api/review/post_comment",
        response_model=ReviewThreadResponse | ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Post one review Comment",
    )
    def post_review_comment(
        self,
        request: PostCommentRequest,
    ) -> ReviewThreadResponse | ReviewThreadUpdateResponse:
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = self.room_lord.find_room(snapshot_id)
            if isinstance(request, NewCodeCommentRequest):
                if isinstance(request.target, ReviewScopeTarget):
                    # The agent batch is the one review-scoped creation
                    # operation. Acting as reviewer makes the Thread wait on
                    # the author, the attention every browser-created code
                    # Thread starts with.
                    (created,) = room.apply_review_batch(
                        (
                            BatchCreate(
                                snapshot_id,
                                CreateReviewThread(
                                    uuid4(),
                                    uuid4(),
                                    uuid4(),
                                    ProfileAuthor(request.profile_id),
                                    request.body,
                                ),
                                "reviewer",
                            ),
                        )
                    )
                    discussion = room.get_thread(
                        snapshot_id, created.thread_id
                    ).discussion()
                    return ReviewThreadResponse.model_validate(discussion)
                thread = room.create_thread(
                    snapshot_id,
                    CreateThread(
                        uuid4(),
                        uuid4(),
                        uuid4(),
                        ProfileAuthor(request.profile_id),
                        self.review_target(request.target),
                        request.body,
                    ),
                )
                discussion = thread.discussion()
                return ReviewThreadResponse.model_validate(discussion)
            update = room.get_thread(
                snapshot_id, UUID(hex=request.thread_id)
            ).add_comment(
                AddComment(
                    uuid4(),
                    uuid4(),
                    ProfileAuthor(request.profile_id),
                    request.body,
                ),
                attention=request.attention,
            )
            return ReviewThreadUpdateResponse.model_validate(update)
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    @routes.post(
        "/api/review/edit_comment",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Edit one review Comment",
    )
    def edit_review_comment(
        self,
        request: EditReviewCommentRequest,
    ) -> ReviewThreadUpdateResponse:
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = self.room_lord.find_room(snapshot_id)
            comment_id = UUID(hex=request.comment_id)
            update = room.thread_for_comment(
                snapshot_id, comment_id
            ).edit_comment(
                comment_id,
                EditComment(
                    uuid4(),
                    ProfileAuthor(request.profile_id),
                    request.body,
                ),
            )
            return ReviewThreadUpdateResponse.model_validate(update)
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    @routes.post(
        "/api/review/delete_comment",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Delete one review Comment",
    )
    def delete_review_comment(
        self,
        request: DeleteReviewCommentRequest,
    ) -> ReviewThreadUpdateResponse:
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            room = self.room_lord.find_room(snapshot_id)
            comment_id = UUID(hex=request.comment_id)
            update = room.thread_for_comment(
                snapshot_id, comment_id
            ).delete_comment(
                comment_id,
                DeleteComment(uuid4(), ProfileAuthor(request.profile_id)),
            )
            return ReviewThreadUpdateResponse.model_validate(update)
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    def change_review_thread_state(
        self,
        *,
        request: ChangeReviewThreadStateRequest | DeleteReviewThreadRequest,
        action: Literal["resolve", "reopen", "delete"],
    ) -> ReviewThreadUpdateResponse:
        try:
            snapshot_id = UUID(hex=request.snapshot_id)
            thread_id = UUID(hex=request.thread_id)
            room = self.room_lord.find_room(snapshot_id)
            thread = room.get_thread(snapshot_id, thread_id)
            # Only resolve and reopen requests can carry an explanation
            # Comment; deletion has no body field and never creates one.
            body = (
                request.body
                if isinstance(request, ChangeReviewThreadStateRequest)
                else None
            )
            command = ChangeThreadState(
                uuid4(),
                ProfileAuthor(request.profile_id),
                uuid4() if body is not None else None,
                body,
            )
            if action == "resolve":
                updated = thread.resolve(command)
            elif action == "reopen":
                updated = thread.reopen(command)
            else:
                updated = thread.delete(command)
            return ReviewThreadUpdateResponse.model_validate(updated)
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc

    @routes.post(
        "/api/review/resolve_thread",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Resolve one review Thread",
    )
    def resolve_review_thread(
        self,
        request: ChangeReviewThreadStateRequest,
    ) -> ReviewThreadUpdateResponse:
        return self.change_review_thread_state(
            request=request,
            action="resolve",
        )

    @routes.post(
        "/api/review/reopen_thread",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Reopen one review Thread",
    )
    def reopen_review_thread(
        self,
        request: ChangeReviewThreadStateRequest,
    ) -> ReviewThreadUpdateResponse:
        return self.change_review_thread_state(
            request=request,
            action="reopen",
        )

    @routes.post(
        "/api/review/delete_thread",
        response_model=ReviewThreadUpdateResponse,
        responses=_REVIEW_ERROR_RESPONSES,
        summary="Delete one review Thread",
    )
    def delete_review_thread(
        self,
        request: DeleteReviewThreadRequest,
    ) -> ReviewThreadUpdateResponse:
        return self.change_review_thread_state(
            request=request,
            action="delete",
        )

    @routes.post(
        "/api/review/upload_attachment",
        response_model=ReviewAttachmentResponse,
        responses=_ATTACHMENT_UPLOAD_RESPONSES,
        summary="Upload one image for review Comments",
    )
    async def upload_review_attachment(
        self,
        request: Request,
        snapshot_id: UUID = Query(
            description="A Snapshot of the Room the image is uploaded to."
        ),
        profile_id: int = Query(gt=0, description="The uploading Profile."),
    ) -> ReviewAttachmentResponse:
        """The request body is the image's bytes. PNG, JPEG, WebP and GIF are
        accepted, decided from the bytes rather than the declared type, up to
        10 MiB. The response's `url` goes into a Comment body as
        `![caption](url)`; the image is readable from every Snapshot of the
        uploading Snapshot's Room. Uploading the same bytes again returns the
        same `url`.
        """
        data = bytearray()
        # Stops reading one byte past the cap, so an oversized body is never
        # held in full.
        async for chunk in request.stream():
            data += chunk
            if len(data) > ATTACHMENT_SIZE_MAX:
                raise _ReviewHttpException(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    ReviewError(
                        "invalid_target",
                        "An attachment holds at most "
                        f"{ATTACHMENT_SIZE_MAX} bytes.",
                    ),
                )

        def store() -> str:
            return self.room_lord.find_room(snapshot_id).store_attachment(
                profile_id, bytes(data)
            )

        try:
            digest = await run_in_threadpool(store)
        except ReviewError as exc:
            raise self.review_http_exception(exc) from exc
        except DirdiffError as exc:
            raise self.review_http_exception(
                ReviewError("invalid_target", str(exc))
            ) from exc
        return ReviewAttachmentResponse(url=f"attachment:{digest}")

    @routes.get(
        "/api/review/attachment",
        response_class=Response,
        responses={HTTPStatus.NOT_FOUND: {"model": ErrorResponse}},
        summary="Read one image uploaded for review Comments",
    )
    def serve_review_attachment(
        self,
        snapshot_id: UUID = Query(
            description="A Snapshot of the Room the image was uploaded to."
        ),
        digest: str = Query(
            pattern=r"^[0-9a-f]{64}$",
            description="The image's SHA-256 digest, from its `attachment:` URL.",
        ),
    ) -> Response:
        """Serves an image uploaded to the Room of the given Snapshot; an image
        uploaded only to another Room is not found.
        """
        try:
            found = self.room_lord.find_room(snapshot_id).attachment(digest)
        except DirdiffError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail=str(exc)
            ) from exc
        if not Some.present(found):
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"No attachment {digest} in this Snapshot's Room.",
            )
        data, media_type = found.value
        return Response(
            content=data,
            media_type=media_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                # Content-addressed and never removed, so this address can
                # never change content once it serves.
                "Cache-Control": "private, max-age=31536000, immutable",
            },
        )
