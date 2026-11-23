import json
import math
import re
import sys
import time
from datetime import UTC
from pathlib import Path
from typing import Annotated, Literal, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import typer
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from dirdiff.room_lord import ATTACHMENT_SIZE_MAX
from dirdiff.util import JsonValue

__all__ = ["agent_app"]

agent_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    help="Review a patch through a running dirdiff server, as an agent.",
)

SessionOption = Annotated[
    Path,
    typer.Option(
        "--session",
        help="This agent's own JSON session file (created by `init`).",
    ),
]

BodyFileOption = Annotated[
    Path | None,
    typer.Option(
        "--body-file",
        help="Read the Comment body from this file (stdin when omitted).",
    ),
]

OptionalBodyFileOption = Annotated[
    Path | None,
    typer.Option(
        "--body-file",
        help="Attach this file's content as a Comment (none when omitted).",
    ),
]

_ACTION_VERBS = {
    "create-thread": "opened a thread",
    "comment": "commented",
    "resolve": "resolved the thread",
    "reopen": "reopened the thread",
    "edit-comment": "edited a comment",
    "delete-comment": "deleted a comment",
    "delete-thread": "deleted the thread",
}

# Largest page size the agent endpoints accept.
_PAGE_LIMIT = 100

# Longest single `wait` hold the server accepts.
_WAIT_MAX_SECONDS = 540

# The HUD shows the Markdown images whose destination is `attachment:` and a
# digest. A caption may hold escaped brackets, as `attach` writes them; the
# destination may sit in angle brackets and be followed by a title. Unlike the
# HUD, this also matches such an image written inside a code span.
_ATTACHMENT_IMAGE = re.compile(
    r"!\[(?:[^\]\\]|\\.)*\]\(\s*"
    r"(?:<attachment:([0-9a-f]{64})>|attachment:([0-9a-f]{64}))"
    r"""(?:\s+(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\)))?"""
    r"\s*\)"
)

# Every type the Room stores an attachment as.
_ATTACHMENT_EXTENSIONS = {
    "image/png": "png",
    "image/apng": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


class Wire(BaseModel):
    """Strict response model: a renamed, missing or extra server field fails
    validation.
    """

    model_config = ConfigDict(extra="forbid")


class Session(BaseModel):
    """Session file content; a missing, unknown or mistyped field makes the file
    malformed.
    """

    model_config = ConfigDict(extra="forbid")
    server_url: str
    agent_uuid: str
    profile_id: int
    name: str
    role: Literal["author", "reviewer"]
    model: str
    harness: str
    project_id: int
    tab: dict[str, JsonValue]
    snapshot_id: str
    snapshot_dir: str
    cursor: int


class OnboardResponse(Wire):
    dirdiff_url: str
    project_id: int | None
    tab: dict[str, JsonValue]
    skill_paths: list[str]
    next_step: str


class ProfileResponse(Wire):
    profile_id: int
    name: str
    model: str
    harness: str


class ManifestFile(Wire):
    left_path: str | None
    right_path: str | None
    change_type: str
    capture_error: str | None
    left_file: str | None
    right_file: str | None


class CaptureResponse(Wire):
    project_id: int
    snapshot_id: str
    tab: dict[str, JsonValue]
    snapshot_path: str
    latest_activity_id: int
    files: list[ManifestFile]


class Author(Wire):
    profile_id: int
    name: str
    is_agent: bool
    model: str | None
    harness: str | None


class Location(Wire):
    path: str
    side: str
    bay_key: str | None
    start_line: int | None
    end_line: int | None


class CommentPreview(Wire):
    body: str | None
    deleted: bool
    truncated: bool


class ThreadSummary(Wire):
    thread_id: str
    status: str
    waiting_on: str
    title: str | None
    location: Location | None
    outdated_reason: str | None
    comment_count: int
    first_comment: CommentPreview
    latest_comment: CommentPreview
    latest_activity_id: int


class Page[Item](Wire):
    items: list[Item]
    page: int
    limit: int
    total: int
    has_more: bool


class ThreadPage(Page[ThreadSummary]):
    through_activity_id: int


class Excerpt(Wire):
    side: str
    start_line: int
    selected_start_line: int
    selected_end_line: int
    lines: list[str]


class Comment(Wire):
    comment_id: str
    author: Author
    body: str | None
    deleted: bool
    revision: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Thread(Wire):
    thread_id: str
    snapshot_id: str
    status: str
    waiting_on: str
    location: Location | None
    original_excerpt: Excerpt | None
    comments: list[Comment]
    page: int
    limit: int
    total_comments: int
    has_more: bool


class Review(Wire):
    tab: dict[str, JsonValue]
    snapshot_id: str
    open_threads: int
    waiting_on_author: int
    waiting_on_reviewer: int


class ReviewsResponse(Wire):
    reviews: list[Review]


class EventComment(Wire):
    comment_id: str
    author: Author
    body: str | None


class Event(Wire):
    activity_id: int
    created_at: AwareDatetime
    tab: dict[str, JsonValue]
    thread_id: str
    title: str | None
    location: Location | None
    action: str
    author: Author
    comment: EventComment | None
    status: str
    waiting_on: str


class ActivityResponse(Wire):
    events: list[Event]
    next_after: int
    has_more: bool


class ActionResult(Wire):
    kind: str
    thread_id: str
    comment_id: str | None
    activity_id: int
    status: str
    waiting_on: str


class ActionsResponse(Wire):
    results: list[ActionResult]


class AttachmentResponse(Wire):
    url: str


def fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(1)


def endpoint(
    server_url: str, path: str, query: dict[str, str | int] | None = None
) -> str:
    if query is None:
        return server_url + path
    return f"{server_url}{path}?{urlencode(query)}"


def fetch[Result: Wire](
    model: type[Result],
    url: str,
    *,
    body: dict[str, JsonValue] | bytes | None = None,
    timeout: float = 60,
) -> Result:
    match body:
        case None:
            request = Request(url)
        case bytes():
            request = Request(
                url,
                data=body,
                headers={"Content-Type": "application/octet-stream"},
            )
        case dict():
            request = Request(
                url,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode()
    except HTTPError as exc:
        fail(f"HTTP {exc.code} from {url}\n{exc.read().decode().strip()}")
    except URLError as exc:
        fail(f"Cannot reach {url}: {exc.reason}")
    try:
        return model.model_validate_json(text)
    except ValidationError as exc:
        fail(f"Unexpected response from {url}:\n{exc}")


def load_session(path: Path) -> Session:
    if not path.is_file():
        fail(f"Session file {path} does not exist; create it with `init`.")
    try:
        return Session.model_validate_json(path.read_text())
    except ValidationError as exc:
        fail(f"Session file {path} is malformed:\n{exc}")


def save_session(path: Path, session: Session) -> None:
    path.write_text(session.model_dump_json(indent=2) + "\n")


def read_body(body_file: Path | None) -> str:
    if body_file is not None:
        if not body_file.is_file():
            fail(f"Body file {body_file} does not exist.")
        text = body_file.read_text()
    else:
        if sys.stdin.isatty() is True:
            fail("No --body-file given and standard input is a terminal.")
        text = sys.stdin.read()
    if text.strip() == "":
        fail("The Comment body is empty.")
    return text


def describe_author(author: Author) -> str:
    if not author.is_agent:
        return f"{author.name} (human)"
    details = [x for x in (author.model, author.harness) if x is not None]
    return f"{author.name} (agent{''.join(', ' + x for x in details)})"


def format_time(moment: AwareDatetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def describe_location(location: Location | None) -> str:
    if location is None:
        return "whole review"
    parts = [location.side]
    if location.bay_key is not None:
        parts.append(f"bay {location.bay_key}")
    if location.start_line is not None:
        parts.append(f"lines {location.start_line}-{location.end_line}")
    return f"{location.path} ({', '.join(parts)})"


def describe_state(status: str, waiting_on: str) -> str:
    if waiting_on == "none":
        return status
    return f"{status}, waiting on {waiting_on}"


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def list_threads(session: Session, status: Literal["open", "all"]) -> None:
    items: list[ThreadSummary] = []
    page = 1
    # Page one omits the pivot and the server chooses it; later pages repeat it
    # so membership and order cannot shift while this loop pages.
    through_activity_id: int | None = None
    while True:
        parameters: dict[str, str | int] = {
            "snapshot_id": session.snapshot_id,
            "status": status,
            "page": page,
            "limit": _PAGE_LIMIT,
        }
        if through_activity_id is not None:
            parameters["through_activity_id"] = through_activity_id
        result = fetch(
            ThreadPage,
            endpoint(session.server_url, "/api/agent/threads", parameters),
        )
        through_activity_id = result.through_activity_id
        items += result.items
        if not result.has_more:
            break
        page += 1
    label = "open threads" if status == "open" else "threads (all statuses)"
    typer.echo(f"{len(items)} {label}")
    for item in items:
        title = "(title deleted)" if item.title is None else item.title
        typer.echo(
            f"- {item.thread_id} [{describe_state(item.status, item.waiting_on)}]"
            f" {title}"
        )
        where = describe_location(item.location)
        if item.outdated_reason is not None:
            where += f"; outdated: {item.outdated_reason}"
        typer.echo(f"    {where}; {item.comment_count} comment(s)")
        latest = item.latest_comment
        if item.comment_count > 1 and latest.body is not None:
            typer.echo(f"    latest: {latest.body.splitlines()[0][:100]}")


def print_snapshot(
    old_snapshot_id: str | None, capture: CaptureResponse
) -> None:
    if old_snapshot_id is None:
        typer.echo(f"Snapshot {capture.snapshot_id}")
    elif old_snapshot_id == capture.snapshot_id:
        typer.echo(f"Snapshot id unchanged: {capture.snapshot_id}")
    else:
        typer.echo(
            f"Snapshot id changed: {old_snapshot_id} -> {capture.snapshot_id}"
        )
    baseline = capture.tab.get("baseline")
    if isinstance(baseline, str):
        typer.echo(f"Head baseline commit: {baseline[:12]}")
    typer.echo(f"Snapshot directory (read-only): {capture.snapshot_path}")
    typer.echo(f"Changed files ({len(capture.files)}):")
    for file in capture.files:
        if file.left_path == file.right_path or file.left_path is None:
            name = file.right_path
        elif file.right_path is None:
            name = file.left_path
        else:
            name = f"{file.left_path} -> {file.right_path}"
        typer.echo(f"- {file.change_type} {name}")
        if file.left_file is not None:
            typer.echo(f"    left:  {file.left_file}")
        if file.right_file is not None:
            typer.echo(f"    right: {file.right_file}")
        if file.capture_error is not None:
            typer.echo(
                f"    capture error (bytes are a placeholder): "
                f"{file.capture_error}"
            )


def apply_action(
    session: Session, action: dict[str, JsonValue]
) -> ActionResult:
    response = fetch(
        ActionsResponse,
        endpoint(session.server_url, "/api/agent/actions"),
        body={"profile_id": session.profile_id, "actions": [action]},
    )
    result = response.results[0]
    typer.echo(
        f"{result.kind}: thread {result.thread_id}"
        + (
            f", comment {result.comment_id}"
            if result.comment_id is not None
            else ""
        )
        + f" -> {describe_state(result.status, result.waiting_on)}"
        + f" (activity {result.activity_id})"
    )
    return result


@agent_app.command()
def init(
    session: SessionOption,
    onboard_url: Annotated[
        str,
        typer.Option(help="Onboarding URL copied from the dirdiff HUD."),
    ],
    role: Annotated[
        Literal["author", "reviewer"],
        typer.Option(help="Role you speak as in this review."),
    ],
    name: Annotated[
        str | None, typer.Option(help="Your unique display name.")
    ] = None,
    model: Annotated[str | None, typer.Option(help="Your model name.")] = None,
    harness: Annotated[
        str | None, typer.Option(help="Your harness name.")
    ] = None,
    identity_from: Annotated[
        Path | None,
        typer.Option(
            help="Session file of a review you already joined; reuse its "
            "identity (UUID, name, model, harness) instead of --name, "
            "--model and --harness."
        ),
    ] = None,
) -> None:
    """Join a review: register a Profile, capture the Tab, write the session.

    Prints the Snapshot directory, the changed files with their captured side
    paths, and the open Threads of the review.

    \f
    # Parameters

    - `session`: Session file to create; must not exist yet.
    - `onboard_url`: Onboarding URL; names the server and the Tab.
    - `role`: `author` or `reviewer`, used by every later action.
    - `name`: Unique display name of a new identity; a name already used by
      another identity is rejected by the server.
    - `model`: Model name of a new identity, shown to other participants.
    - `harness`: Harness name of a new identity, shown to other participants.
    - `identity_from`: Existing session file whose agent UUID, name, model, and
      harness are reused, so one agent is one Profile across several reviews.
      The new session file still belongs to this review alone.

    # Failures

    An existing session file, an unreachable server, an invalid URL, or a name
    already taken exits 1; no session file is written then. Giving
    `--identity-from` together with `--name`, `--model`, or `--harness`, or
    omitting any of the three without `--identity-from`, exits 1.
    """

    if session.exists():
        fail(
            f"Session file {session} already exists. Keep using it, or choose "
            "another path; a second `init` would create a second identity."
        )
    if identity_from is not None:
        if name is not None or model is not None or harness is not None:
            fail(
                "--identity-from takes the identity from that session file; "
                "do not combine it with --name, --model or --harness."
            )
        known = load_session(identity_from)
        agent_uuid = known.agent_uuid
        name = known.name
        model = known.model
        harness = known.harness
    else:
        if name is None or model is None or harness is None:
            fail(
                "A first `init` needs --name, --model and --harness; to join "
                "another review under the same identity, pass --identity-from "
                "with the session file of a review you already joined."
            )
        agent_uuid = uuid4().hex
    onboard = fetch(OnboardResponse, onboard_url)
    # Capture first: a failing capture (for example a missing branch) must not
    # leave a registered Profile whose UUID no session file records.
    capture = fetch(
        CaptureResponse,
        endpoint(onboard.dirdiff_url, "/api/agent/capture"),
        body={"tab": onboard.tab},
    )
    profile = fetch(
        ProfileResponse,
        endpoint(onboard.dirdiff_url, "/api/agent/profile"),
        body={
            "agent_uuid": agent_uuid,
            "name": name,
            "model": model,
            "harness": harness,
        },
    )
    state = Session(
        server_url=onboard.dirdiff_url,
        agent_uuid=agent_uuid,
        profile_id=profile.profile_id,
        name=name,
        role=role,
        model=model,
        harness=harness,
        project_id=capture.project_id,
        tab=onboard.tab,
        snapshot_id=capture.snapshot_id,
        snapshot_dir=capture.snapshot_path,
        cursor=capture.latest_activity_id,
    )
    save_session(session, state)
    typer.echo(f"Session written to {session} (profile {state.profile_id}).")
    typer.echo(f"Tab: {json.dumps(state.tab, separators=(',', ':'))}")
    print_snapshot(None, capture)
    list_threads(state, "open")


@agent_app.command()
def capture(session: SessionOption) -> None:
    """Capture the session's Tab again and update the session.

    Reports whether the Snapshot id changed and lists the changed files with
    their captured side paths. The activity cursor is not touched.

    \f
    # Parameters

    - `session`: Existing session file.

    # Failures

    Server failures exit 1; the session is then left as it was.
    """

    state = load_session(session)
    result = fetch(
        CaptureResponse,
        endpoint(state.server_url, "/api/agent/capture"),
        body={"tab": state.tab},
    )
    old_snapshot_id = state.snapshot_id
    state.snapshot_id = result.snapshot_id
    state.snapshot_dir = result.snapshot_path
    save_session(session, state)
    print_snapshot(old_snapshot_id, result)


@agent_app.command()
def reviews(session: SessionOption) -> None:
    """List the reviews (Rooms) of the session's repository.

    Shows each review's Tab, newest Snapshot id, and how many open Threads wait
    on the author and on the reviewer.

    \f
    # Parameters

    - `session`: Existing session file.
    """

    state = load_session(session)
    result = fetch(
        ReviewsResponse,
        endpoint(
            state.server_url,
            "/api/agent/reviews",
            {"project_id": state.project_id},
        ),
    )
    typer.echo(
        f"{len(result.reviews)} review(s) in repository {state.project_id}"
    )
    for review in result.reviews:
        mine = (
            " (this session)" if review.snapshot_id == state.snapshot_id else ""
        )
        typer.echo(f"- {json.dumps(review.tab, separators=(',', ':'))}{mine}")
        typer.echo(
            f"    snapshot {review.snapshot_id}; {review.open_threads} open; "
            f"waiting on author {review.waiting_on_author}, "
            f"on reviewer {review.waiting_on_reviewer}"
        )


@agent_app.command()
def threads(
    session: SessionOption,
    all_statuses: Annotated[
        bool,
        typer.Option("--all", help="Include resolved and deleted Threads."),
    ] = False,
) -> None:
    """List the Threads of the session's review.

    Open Threads only, unless `--all` adds resolved and deleted ones.

    \f
    # Parameters

    - `session`: Existing session file.
    - `all_statuses`: Whether to list every status.
    """

    list_threads(load_session(session), "all" if all_statuses else "open")


@agent_app.command()
def show(session: SessionOption, thread_id: str) -> None:
    """Print one Thread completely: location, excerpt, state, all Comments.

    Images a Comment shows are saved under `attachments/`, beside the session
    file, and their paths are printed after that Comment's body, so they can
    be opened as files.

    \f
    # Parameters

    - `session`: Existing session file.
    - `thread_id`: Thread id from `threads`, `news`, or an action result.
    """

    state = load_session(session)
    comments: list[Comment] = []
    page = 1
    while True:
        result = fetch(
            Thread,
            endpoint(
                state.server_url,
                f"/api/agent/thread/{thread_id}",
                {"page": page, "limit": _PAGE_LIMIT},
            ),
        )
        comments += result.comments
        if not result.has_more:
            break
        page += 1
    typer.echo(
        f"Thread {result.thread_id} "
        f"[{describe_state(result.status, result.waiting_on)}]"
    )
    typer.echo(
        f"Location: {describe_location(result.location)} "
        f"(as created in snapshot {result.snapshot_id})"
    )
    excerpt = result.original_excerpt
    if excerpt is not None:
        typer.echo("Excerpt (> marks the selected lines):")
        for offset, line in enumerate(excerpt.lines):
            number = excerpt.start_line + offset
            selected = (
                excerpt.selected_start_line
                <= number
                <= excerpt.selected_end_line
            )
            typer.echo(f"{'>' if selected else ' '} {number:5d} {line}")
    typer.echo(f"Comments ({len(comments)}):")
    for index, comment in enumerate(comments, start=1):
        flags = f", revision {comment.revision}" if comment.revision else ""
        typer.echo(
            f"[{index}] comment {comment.comment_id} by "
            f"{describe_author(comment.author)} at "
            f"{format_time(comment.created_at)}{flags}"
        )
        if comment.deleted or comment.body is None:
            typer.echo("    (deleted)")
            continue
        typer.echo(indent(comment.body, "    "))
        digests = dict.fromkeys(
            angled or bare
            for angled, bare in _ATTACHMENT_IMAGE.findall(comment.body)
        )
        for digest in digests:
            url = endpoint(
                state.server_url,
                "/api/review/attachment",
                {"snapshot_id": result.snapshot_id, "digest": digest},
            )
            try:
                with urlopen(url, timeout=60) as response:
                    media_type = response.headers.get_content_type()
                    data = response.read()
            except HTTPError as exc:
                # The HUD shows a broken image for a reference no upload
                # matches; the Thread itself still reads.
                detail = exc.read().decode().strip()
                typer.echo(
                    f"    image attachment:{digest}: HTTP {exc.code} {detail}"
                )
                continue
            except URLError as exc:
                fail(f"Cannot reach {url}: {exc.reason}")
            extension = _ATTACHMENT_EXTENSIONS.get(media_type)
            if extension is None:
                fail(f"Unexpected image type {media_type} from {url}.")
            directory = session.parent / "attachments"
            directory.mkdir(exist_ok=True)
            saved = (directory / f"{digest}.{extension}").resolve()
            saved.write_bytes(data)
            typer.echo(f"    image: {saved}")


def report_activity(session_path: Path, wait_seconds: int | None) -> None:
    state = load_session(session_path)
    # Own events are skipped but the cursor still moves past them; printing
    # them would make `wait` return right after every post.
    start = state.cursor
    deadline = time.monotonic() + (0 if wait_seconds is None else wait_seconds)
    events: list[Event] = []
    own = 0
    while True:
        if wait_seconds is None:
            url = endpoint(
                state.server_url,
                "/api/agent/activity",
                {
                    "project_id": state.project_id,
                    "after": state.cursor,
                    "limit": _PAGE_LIMIT,
                },
            )
            timeout = 60.0
        else:
            hold = max(1, math.ceil(deadline - time.monotonic()))
            url = endpoint(
                state.server_url,
                "/api/agent/wait",
                {
                    "project_id": state.project_id,
                    "after": state.cursor,
                    "timeout": hold,
                    "limit": _PAGE_LIMIT,
                },
            )
            timeout = hold + 30.0
        try:
            result = fetch(ActivityResponse, url, timeout=timeout)
        except ConnectionError:
            # A server killed mid-wait drops the held connection.
            if wait_seconds is None:
                raise
            fail(
                "The server went away during the wait (it stopped or "
                "restarted); call `wait` again."
            )
        state.cursor = result.next_after
        for event in result.events:
            if event.author.profile_id == state.profile_id:
                own += 1
            else:
                events.append(event)
        if result.has_more:
            continue
        if (
            len(events) > 0
            or wait_seconds is None
            or time.monotonic() >= deadline
        ):
            break
    save_session(session_path, state)
    skipped = f" ({own} of your own skipped)" if own > 0 else ""
    if len(events) == 0:
        typer.echo(
            f"No new activity; cursor {start} -> {state.cursor}{skipped}."
        )
        return
    typer.echo(
        f"{len(events)} new event(s); cursor {start} -> {state.cursor}{skipped}"
    )
    for event in events:
        typer.echo(
            f"#{event.activity_id} {format_time(event.created_at)} "
            f"{describe_author(event.author)} {_ACTION_VERBS[event.action]}"
        )
        title = "(title deleted)" if event.title is None else event.title
        typer.echo(
            f"  thread {event.thread_id} {title} "
            f"[{describe_state(event.status, event.waiting_on)}]"
        )
        typer.echo(f"  at {describe_location(event.location)}")
        if event.tab != state.tab:
            typer.echo(
                f"  in another review: "
                f"{json.dumps(event.tab, separators=(',', ':'))}"
            )
        if event.comment is not None:
            typer.echo(f"  comment {event.comment.comment_id}:")
            if event.comment.body is None:
                typer.echo("      (deleted)")
            else:
                typer.echo(indent(event.comment.body, "      "))


@agent_app.command()
def news(session: SessionOption) -> None:
    """Print activity by other participants since the cursor, once.

    Advances the session cursor past everything it read. Your own actions are
    counted but not listed.

    \f
    # Parameters

    - `session`: Existing session file.
    """

    report_activity(session, None)


@agent_app.command()
def wait(
    session: SessionOption,
    timeout: Annotated[
        int,
        typer.Option(
            min=1,
            max=_WAIT_MAX_SECONDS,
            help="Seconds to hold for activity by others (1 to 540).",
        ),
    ],
) -> None:
    """Hold until another participant acts, then print what happened.

    Same output and cursor handling as `news`, but waits up to `--timeout`
    seconds for foreign activity instead of returning at once. Prints that
    nothing happened when the time runs out; call it again to keep waiting.

    \f
    # Parameters

    - `session`: Existing session file.
    - `timeout`: Total seconds to hold.
    """

    report_activity(session, timeout)


@agent_app.command()
def find(
    session: SessionOption,
    path: Annotated[
        str | None,
        typer.Option(help="Repository path of the file to comment on."),
    ] = None,
    side: Annotated[
        Literal["left", "right"] | None,
        typer.Option(help="Side of the diff that --path names."),
    ] = None,
    lines: Annotated[
        str | None,
        typer.Option(help="Inclusive one-based bay-local range, as A-B."),
    ] = None,
    bay: Annotated[
        str | None,
        typer.Option(
            help="Bay key: flatfile for ordinary text, the cell id for a "
            "notebook cell, image or blob for non-text files."
        ),
    ] = None,
    review: Annotated[
        bool,
        typer.Option("--review", help="Open a Thread on the whole review."),
    ] = False,
    body_file: BodyFileOption = None,
) -> None:
    """Open a Thread on a code range, or on the whole review.

    The first Comment is read from `--body-file` or standard input. The Thread
    then waits on the other role.

    \f
    # Parameters

    - `session`: Existing session file; supplies the role and Snapshot.
    - `path`: Repository path, as listed by `capture`.
    - `side`: `left` or `right`; must be a present side of the file.
    - `lines`: `A-B` inside the bay; counted in the captured side's bay text.
    - `bay`: Bay key. Required for code because the CLI cannot tell ordinary
      text from notebooks or images.
    - `review`: Make a review-scoped Thread instead of a code Thread.
    - `body_file`: File holding the first Comment.

    # Failures

    Passing `--review` together with `--path`, `--side`, `--lines`, or `--bay`,
    or omitting any of the four code options without `--review`, exits 1.
    """

    state = load_session(session)
    code = (path, side, lines, bay)
    action: dict[str, JsonValue] = {
        "kind": "create-thread",
        "snapshot_id": state.snapshot_id,
        "role": state.role,
    }
    if review:
        if any(x is not None for x in code):
            fail(
                "--review cannot be combined with --path/--side/--lines/--bay."
            )
    else:
        if path is None or side is None or lines is None or bay is None:
            fail(
                "A code Thread needs --path, --side, --lines and --bay "
                "(or use --review)."
            )
        span = re.fullmatch(r"(\d+)-(\d+)", lines)
        if span is None:
            fail(f"--lines must look like 10-12, got {lines!r}.")
        action["path"] = path
        action["side"] = side
        action["bay"] = {
            "bay_key": bay,
            "start_line": int(span[1]),
            "end_line": int(span[2]),
        }
    action["body"] = read_body(body_file)
    apply_action(state, action)


@agent_app.command()
def attach(
    session: SessionOption,
    file: Annotated[Path, typer.Argument(help="PNG, JPEG, WebP or GIF image.")],
    caption: Annotated[
        str, typer.Option(help="Alternative text of the image.")
    ] = "",
) -> None:
    """Upload an image and print the Markdown that shows it in a Comment.

    Paste the printed `![caption](attachment:...)` line into a Comment body;
    the image is shown wherever this review's Threads are read. The server
    decides the type from the bytes: PNG, JPEG, WebP or GIF, at most 10 MiB.
    Uploading the same image again prints the same line.

    \f
    # Parameters

    - `session`: Existing session file; supplies the Profile and Snapshot.
    - `file`: Image to upload.
    - `caption`: Alternative text, kept literally; one line.
    """

    state = load_session(session)
    if not file.is_file():
        fail(f"Image file {file} does not exist.")
    if "\n" in caption or "\r" in caption:
        fail("--caption must be one line.")
    # Reading one byte past the cap bounds memory and refuses before sending:
    # the server stops reading at the cap, which surfaces here as a broken
    # pipe instead of its explanation.
    with file.open("rb") as image:
        data = image.read(ATTACHMENT_SIZE_MAX + 1)
    if len(data) > ATTACHMENT_SIZE_MAX:
        fail(f"{file} is larger than {ATTACHMENT_SIZE_MAX} bytes.")
    result = fetch(
        AttachmentResponse,
        endpoint(
            state.server_url,
            "/api/review/upload_attachment",
            {"snapshot_id": state.snapshot_id, "profile_id": state.profile_id},
        ),
        body=data,
    )
    # Every ASCII punctuation character is escaped, because Markdown resolves
    # entities, emphasis and brackets inside alternative text too.
    escaped = re.sub(r"([!-/:-@\[-`{-~])", r"\\\1", caption)
    typer.echo(f"![{escaped}]({result.url})")


@agent_app.command()
def comment(
    session: SessionOption, thread_id: str, body_file: BodyFileOption = None
) -> None:
    """Add a Comment to a Thread; it then waits on the other role.

    \f
    # Parameters

    - `session`: Existing session file; supplies the role.
    - `thread_id`: Thread to answer.
    - `body_file`: File holding the body, or standard input when omitted.
    """

    state = load_session(session)
    apply_action(
        state,
        {
            "kind": "comment",
            "thread_id": thread_id,
            "role": state.role,
            "body": read_body(body_file),
        },
    )


@agent_app.command()
def resolve(
    session: SessionOption,
    thread_id: str,
    body_file: OptionalBodyFileOption = None,
) -> None:
    """Resolve an open Thread, optionally with a closing Comment.

    \f
    # Parameters

    - `session`: Existing session file.
    - `thread_id`: Thread to resolve.
    - `body_file`: Optional file holding a closing Comment.
    """

    state = load_session(session)
    action: dict[str, JsonValue] = {"kind": "resolve", "thread_id": thread_id}
    if body_file is not None:
        action["body"] = read_body(body_file)
    apply_action(state, action)


@agent_app.command()
def reopen(
    session: SessionOption,
    thread_id: str,
    body_file: OptionalBodyFileOption = None,
) -> None:
    """Reopen a resolved Thread, optionally with a Comment.

    \f
    # Parameters

    - `session`: Existing session file; supplies the role.
    - `thread_id`: Thread to reopen.
    - `body_file`: Optional file holding a Comment.
    """

    state = load_session(session)
    action: dict[str, JsonValue] = {
        "kind": "reopen",
        "thread_id": thread_id,
        "role": state.role,
    }
    if body_file is not None:
        action["body"] = read_body(body_file)
    apply_action(state, action)


@agent_app.command()
def edit(
    session: SessionOption,
    thread_id: str,
    comment_id: str,
    body_file: BodyFileOption = None,
) -> None:
    """Replace the text of your own Comment.

    The API addresses a Comment through its Thread, so both ids are required;
    `show THREAD_ID` prints the Comment ids.

    \f
    # Parameters

    - `session`: Existing session file.
    - `thread_id`: Thread containing the Comment.
    - `comment_id`: Comment to edit; only its author may.
    - `body_file`: File holding the new body, or standard input when omitted.
    """

    state = load_session(session)
    apply_action(
        state,
        {
            "kind": "edit-comment",
            "thread_id": thread_id,
            "comment_id": comment_id,
            "body": read_body(body_file),
        },
    )


@agent_app.command(name="delete-comment")
def delete_comment(
    session: SessionOption, thread_id: str, comment_id: str
) -> None:
    """Tombstone a Comment.

    \f
    # Parameters

    - `session`: Existing session file.
    - `thread_id`: Thread containing the Comment.
    - `comment_id`: Comment to delete.
    """

    apply_action(
        load_session(session),
        {
            "kind": "delete-comment",
            "thread_id": thread_id,
            "comment_id": comment_id,
        },
    )


@agent_app.command(name="delete-thread")
def delete_thread(session: SessionOption, thread_id: str) -> None:
    """Delete a Thread; its history stays readable but it accepts nothing more.

    Only on explicit human instruction.

    \f
    # Parameters

    - `session`: Existing session file.
    - `thread_id`: Thread to delete.
    """

    apply_action(
        load_session(session),
        {"kind": "delete-thread", "thread_id": thread_id},
    )
