"""The image format: a File the browser can display but nobody can read.

An image File composes one heading-less frame holding two bays. The `image` bay
is the picture, and it is the frame's body: a reviewer who opened an image came
to look at it, so it is always shown. Beside it, open by default and
collapsible, is a `text` bay stating what is known about the bytes — media
type, size, digest. Three lines cost nothing to read, so they are stated rather
than hidden behind a disclosure, and a reviewer who does not want them shuts
the bay.

Neither bay answers the other's question. The picture answers "does it look
different", and a re-encode that rewrites every byte can look identical. The
facts answer "did it actually change, and to what", and two renderings that
differ visibly have different digests for a reason worth reading. So both are
composed, always, and the reviewer decides which one to read.

Public interface: `image_media_type()`, which is both the classification test
and the answer classification needs, and `image_bays()`, which yields those two
bays in that order.

What this module does not own: rendering, scaling, or decoding. It never looks
inside the bytes, never asks how large the picture is, and never produces a
thumbnail — the bytes it yields are the captured bytes, and the browser is the
thing that turns them into a picture. It does not own the wording of the facts
either; `base.py` writes those, because a blob File states the same three facts
the same way. It also does not own classification order; `composer.py` decides
when to ask, and calls `image_bays()` once the answer is final.
"""

from __future__ import annotations

from collections.abc import Iterator

from dirdiff.engines import DiffSide
from dirdiff.formats.base import (
    IMAGE_BAY_KEY,
    IMAGE_FACTS_BAY_KEY,
    MEDIA_FACTS_PATH_HINT,
    Bay,
    BayContext,
    ImageBay,
    MediaSide,
    TextBay,
    media_facts,
    whole_file_change,
)

__all__ = [
    "image_bays",
    "image_media_type",
]


_IMAGE_MEDIA_TYPES = {
    ".apng": "image/apng",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
"""The extensions this project treats as images, and what each one is.

Deliberately a closed list of what browsers display natively without a decoder
of ours. `image/svg+xml` is absent and stays absent: an SVG is
author-controlled markup that the frontend would have to execute to display,
which is the security question `formats.md` records for HTML outputs, and it
is also perfectly readable as text — a flatfile diff of an SVG tells a reviewer
more than a picture of it does.
"""


def image_media_type(path: str | None) -> str | None:
    """Return the media type this path claims to be, or `None` if not an image.

    Classification is by filename extension, matched case-insensitively. That is
    what the repository asserts the file is, and for a review tool showing a
    reviewer what a repository contains it is the honest answer: a `.png` whose
    bytes are a JPEG is a fact about the repository, not about this function.

    An absent path is not an image, which is how a side the File does not have
    answers.
    """
    # TODO: extension matching is the simple answer and it is wrong for a file
    # with no extension, a file whose extension lies, and every format not on
    # the list above. Reading the leading magic bytes would settle the first
    # two, and a third-party library such as `filetype` or `puremagic` would
    # settle all three. Neither is worth a dependency until a real review runs
    # into it; when one does, this function is the only thing that changes.
    if path is None:
        return None
    lowered = path.lower()
    for suffix, media_type in _IMAGE_MEDIA_TYPES.items():
        if lowered.endswith(suffix):
            return media_type
    return None


def image_bays(
    left: bytes | None,
    right: bytes | None,
    context: BayContext,
    *,
    left_media_type: str | None,
    right_media_type: str | None,
) -> Iterator[Bay]:
    """Yield the picture bay and the facts bay an image File composes into.

    The two media types are the ones classification already derived from the two
    paths, passed in rather than derived again, so what was classified and what
    is served cannot disagree. A present side requires its media type: a side
    whose path did not name an image is not an image File, and classification
    must not have called this.

    Both bays follow the whole-File rule for `change`, and read it from
    different content on purpose. The picture's is read from the exact bytes, so
    two byte-identical sides are `unchanged` and the bay takes no navigation
    stop. The facts bay's is read from the facts text, which is equivalent
    whenever content changed — the digest is total — and strictly better for a
    rename that only changes the declared media type: the type row honestly
    reads as changed while the picture reads unchanged.
    """
    left_side: MediaSide | None = None
    if left is not None:
        assert left_media_type is not None, (
            "an image File's captured left side must have a media type"
        )
        left_side = MediaSide(media_type=left_media_type, data=left)
    right_side: MediaSide | None = None
    if right is not None:
        assert right_media_type is not None, (
            "an image File's captured right side must have a media type"
        )
        right_side = MediaSide(media_type=right_media_type, data=right)
    # One frame, and nothing to name above a File that is entirely one picture,
    # so both bays are keyed "file" and carry no heading, exactly as a
    # flatfile's one bay does.
    yield ImageBay(
        frame_key="file",
        heading=None,
        bay_key=IMAGE_BAY_KEY,
        label="Image",
        detail=None,
        collapsible=False,
        default_expanded=True,
        change=whole_file_change(left, right),
        left=left_side,
        right=right_side,
    )
    left_facts = media_facts(left_side)
    right_facts = media_facts(right_side)
    yield TextBay(
        frame_key="file",
        heading=None,
        bay_key=IMAGE_FACTS_BAY_KEY,
        label="Media facts",
        detail=None,
        collapsible=True,
        default_expanded=True,
        change=whole_file_change(left_facts, right_facts),
        left_label=context.left_label,
        right_label=context.right_label,
        left=DiffSide(
            exists=left_side is not None,
            text=left_facts,
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        right=DiffSide(
            exists=right_side is not None,
            text=right_facts,
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
    )
