"""Seeded splice fuzzing for the tokendiff engine over the preset corpus.

Each difftastic preset side is mutated by deterministic random slice
deletions, duplications, swaps, and cross-file insertions. These are shapes real
edits and merge accidents take, and every mutant pair must keep the
engine's core guarantees: exact source reproduction, ordered line coverage,
token partitioning, cross-side unchanged agreement, and status derivation.
The generator is seeded per case, so a failure reproduces exactly.
"""

import random
import zlib
from pathlib import Path
from typing import Literal

import pytest
from _pytest.mark.structures import ParameterSet

from dirdiff.engines import (
    DiffEngineRow,
    DiffSide,
    InlineToken,
    TokenDiffEngine,
)

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "diff"
"""Human-readable source corpus from which deterministic mutants are derived.

Every old and new side becomes an independent fuzz subject, preserving its
fixture-derived id for reproducible failures.
"""
MUTANTS_PER_SOURCE = 20
"""Number of independently seeded mutation pairs checked per corpus side.

The bound keeps this deliberately slow suite finite while sampling different
splice shapes from every source.
"""
Side = Literal["left", "right"]
"""Original or mutated source side reconstructed from engine rows.

Fuzz assertions use it to select line numbers, texts, and inline tokens from
one result. It does not encode mutation direction or row status.
"""


def _preset_sources() -> list[ParameterSet]:
    """Collect each readable preset side as an independent mutation seed.

    Old and new contents receive side-qualified pytest IDs, so identical text
    from different real files remains separate corpus coverage. Binary or
    absent sides never enter the text mutator.

    # Usage

    Use as the seed corpus for deterministic mutation cases. Each parameter id
    already identifies its preset and side.

    # Failures

    Raises `AssertionError` when a collected preset side has no source suffix.
    """
    sources: list[ParameterSet] = []
    for preset in sorted(PRESETS_ROOT.glob("*/*")):
        if not preset.is_dir():
            continue
        for path in sorted(preset.glob("old.*")) + sorted(preset.glob("new.*")):
            sources.append(
                pytest.param(
                    path.read_text(encoding="utf-8"),
                    id=f"{preset.parent.name}-{preset.name}-{path.stem}",
                )
            )
    assert sources != []
    return sources


SOURCES = _preset_sources()
"""Stable collection-time parametrization of every old and new preset side.

Each id retains its fixture origin so a failed deterministic seed is easy to
locate and rerun.
"""


def _mutant(source: str, rng: random.Random) -> str:
    """Apply a bounded sequence of slice edits to one document.

    The supplied generator decides deletion, duplication, copied replacement,
    or whitespace/word insertion. Given the same state, output is exact and
    reproducible.

    # Parameters

    - `source`: Original fixture text to mutate without modifying the fixture.
    - `rng`: Case-seeded generator used for every edit choice.

    # Usage

    Seed `rng` from the case identity and call repeatedly to reproduce the same
    sequence of generated documents.
    """
    text = source
    for _ in range(rng.randint(1, 4)):
        if text == "":
            text = "seed\n"
        start = rng.randrange(len(text) + 1)
        end = min(len(text), start + rng.randrange(1, 80))
        action = rng.randrange(4)
        if action == 0:
            text = text[:start] + text[end:]
        elif action == 1:
            text = text[:start] + text[start:end] + text[start:]
        elif action == 2:
            other_start = rng.randrange(len(text) + 1)
            other_end = min(len(text), other_start + rng.randrange(1, 80))
            text = text[:start] + text[other_start:other_end] + text[end:]
        else:
            filler = rng.choice(["\n", "    ", " ", "word_one two\n", "\t"])
            text = text[:start] + filler + text[end:]
    return text


def _check_invariants(left: str, right: str) -> None:
    """Assert every core row guarantee for one rendered pair.

    # Parameters

    - `left`: Complete generated old document.
    - `right`: Complete generated new document.

    # Usage

    Call for every generated source pair after mutation. This helper uses the
    public `TokenDiffEngine` result rather than internal edit steps.

    # Failures

    Raises `AssertionError` when source replay, line numbering, token
    partitioning, shared text, row status, or summary counts violate the engine
    contract.
    """

    def _side_no(row: DiffEngineRow, side: Side) -> int | None:
        """Read and narrow one side's line coordinate.

        # Parameters

        - `row`: Engine row containing side-local coordinates.
        - `side`: Side whose coordinate is selected.

        # Returns

        - `int`: The selected side's one-based line number.
        - `None`: This row has no content on the selected side. The invariant
          checks must treat it as a one-sided row.

        # Failures

        Raises `AssertionError` when a present coordinate is not an integer.
        """
        number = row.get(f"{side}_no")
        assert number is None or isinstance(number, int)
        return number

    def _side_text(row: DiffEngineRow, side: Side) -> str:
        """Read and narrow one side's exact row text.

        # Parameters

        - `row`: Engine row containing source text.
        - `side`: Side whose text is selected.

        # Failures

        Raises `AssertionError` when the selected row text is not a string.
        """
        text = row.get(f"{side}_text")
        assert isinstance(text, str)
        return text

    def _side_tokens(row: DiffEngineRow, side: Side) -> list[InlineToken]:
        """Read and narrow one side's token partition.

        # Parameters

        - `row`: Engine row containing both token lists.
        - `side`: Side whose partition is selected.

        # Failures

        Raises `AssertionError` when the selected token value is not a list.
        """
        tokens = row.get(f"{side}_tokens")
        assert isinstance(tokens, list)
        return tokens

    rows = TokenDiffEngine().render_diff(
        old=DiffSide(exists=True, text=left),
        new=DiffSide(exists=True, text=right),
    )["rows"]
    sides: list[tuple[Side, str]] = [("left", left), ("right", right)]
    for side, source in sides:
        lines = [
            _side_text(row, side)
            for row in rows
            if _side_no(row, side) is not None
        ]
        joined = "\n".join(lines)
        if source.endswith("\n"):
            joined += "\n"
        assert joined == source
        numbers = [
            _side_no(row, side)
            for row in rows
            if _side_no(row, side) is not None
        ]
        assert numbers == list(range(1, len(numbers) + 1))
    for row in rows:
        for side, _ in sides:
            tokens = _side_tokens(row, side)
            if tokens == []:
                continue
            assert _side_no(row, side) is not None
            assert "".join(token["text"] for token in tokens) == _side_text(
                row, side
            )
            for token in tokens:
                assert token["text"] != ""
                assert token["is_ws"] == token["text"].isspace()
        left_unchanged = "".join(
            token["text"]
            for token in row["left_tokens"]
            if token["status"] == "unchanged"
        )
        right_unchanged = "".join(
            token["text"]
            for token in row["right_tokens"]
            if token["status"] == "unchanged"
        )
        assert left_unchanged == right_unchanged
        if row["left_no"] is None:
            assert row["status"] == "insert"
            assert row["left_tokens"] == [] and row["right_tokens"] == []
        elif row["right_no"] is None:
            assert row["status"] == "delete"
            assert row["left_tokens"] == [] and row["right_tokens"] == []
        elif row["left_tokens"] + row["right_tokens"] == []:
            assert row["left_text"] == row["right_text"]
            assert row["status"] == "equal"
        else:
            content_changed = any(
                token["status"] != "unchanged" and token["is_ws"] is False
                for token in row["left_tokens"] + row["right_tokens"]
            )
            assert row["status"] == ("replace" if content_changed else "equal")


@pytest.mark.parametrize("source", SOURCES)
def test_tokendiff_fuzzed_mutants_keep_invariants(source: str) -> None:
    """Exercise deterministic splice mutations against every row invariant.

    Each preset source seeds a stable random stream. Every mutant must replay
    both inputs exactly, retain valid consecutive line numbers, and keep token
    text/status consistent; the loop tests more than mere engine completion.
    """
    # zlib.crc32 keeps the seed stable across processes, unlike hash().
    rng = random.Random(f"tokendiff:{zlib.crc32(source.encode('utf-8'))}")
    for _ in range(MUTANTS_PER_SOURCE):
        mutant = _mutant(source, rng)
        _check_invariants(source, mutant)
        _check_invariants(mutant, source)
