"""Integration checks for repository path and ref loading.

These tests use real Git repositories to verify rename detection and explicit
remote-ref comparisons through the backend/service boundary.  They should keep
Git setup local to temporary directories and assert public diff-path contracts,
not private command strings.
"""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from helpers import TextDiffService

from dirdiff.backend import GitBackend
from dirdiff.engines import DirdiffError


def test_detects_git_reported_repo_renames(tmp_path: Path) -> None:
    """Git rename detection preserves both paths as one File identity.

    A worktree move with unchanged bytes must not degrade into independent
    deletion and addition entries.
    """
    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    tracked_file = tmp_path / "alpha.txt"
    tracked_file.write_text("one\ntwo\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "alpha.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "mv", "alpha.txt", "renamed.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    paths = service.backend.repo_diff(left="HEAD", right="worktree").paths

    assert len(paths) == 1
    assert paths[0].display_name == "alpha.txt -> renamed.txt"
    assert paths[0].change_type == "rename"
    assert paths[0].left_path == "alpha.txt"
    assert paths[0].right_path == "renamed.txt"


def test_branch_review_uses_explicit_remote_refs(tmp_path: Path) -> None:
    """Remote Branch Review selections retain their configured remote names.

    Resolution uses the explicitly selected remote ref and rejects the bare
    branch spelling, preventing an ambiguous local or remote choice.
    """
    subprocess.run(
        ["git", "init", "-b", "master"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    tracked_file = tmp_path / "alpha.txt"
    tracked_file.write_text("one\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "alpha.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    for remote_name in ["upstream", "origin", "cjgrand1"]:
        subprocess.run(
            [
                "git",
                "remote",
                "add",
                remote_name,
                f"https://example.invalid/{remote_name}.git",
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/upstream/main", base_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    tracked_file.write_text("two\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "second"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    branch_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/upstream/rich-text", branch_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/rich-text", base_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/cjgrand1/rich-text", base_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))
    ref_choices = service.list_ref_choices()
    resolved_base_branch, merge_base, normalized_branch, review_commit = (
        service.resolve_branch_diff_sides(
            base_selection={
                "source": "remote",
                "remote": "upstream",
                "branch": "main",
            },
            review_selection={
                "source": "remote",
                "remote": "upstream",
                "branch": "rich-text",
            },
        )
    )

    assert ref_choices["remotes"] == ["cjgrand1", "origin", "upstream"]
    remote_branches = ref_choices["remote_branches"]
    assert {
        "structured": {
            "remote": "cjgrand1",
            "branch": "rich-text",
        },
        "gitref": "cjgrand1/rich-text",
    } in remote_branches
    assert {
        "structured": {
            "remote": "origin",
            "branch": "rich-text",
        },
        "gitref": "origin/rich-text",
    } in remote_branches
    assert {
        "structured": {
            "remote": "upstream",
            "branch": "main",
        },
        "gitref": "upstream/main",
    } in remote_branches
    assert {
        "structured": {
            "remote": "upstream",
            "branch": "rich-text",
        },
        "gitref": "upstream/rich-text",
    } in remote_branches
    assert resolved_base_branch == "upstream/main"
    assert merge_base == base_commit
    assert normalized_branch == "upstream/rich-text"
    assert review_commit == branch_commit

    try:
        service.normalize_side("rich-text")
    except ValueError as exc:
        assert str(exc) == "Unknown Git ref: rich-text"
    else:
        raise AssertionError(
            "expected remote ref to require explicit remote name"
        )


def test_ref_choices_use_configured_remote_names_with_slashes(
    tmp_path: Path,
) -> None:
    """Ref discovery separates a slash-containing remote from its branch name.

    The structured choice retains `team/origin` as one configured remote rather
    than assuming the first path component is always the remote.
    """
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    tracked_file = tmp_path / "alpha.txt"
    tracked_file.write_text("one\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "alpha.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "team/origin",
            "https://example.invalid/repo.git",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/team/origin/main", base_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    service = TextDiffService(GitBackend.discover(cwd=tmp_path))

    ref_choices = service.list_ref_choices()

    assert ref_choices["remotes"] == ["team/origin"]
    assert {
        "structured": {
            "remote": "team/origin",
            "branch": "main",
        },
        "gitref": "team/origin/main",
    } in ref_choices["remote_branches"]


def test_numstat_parser_reads_changed_rename_records(tmp_path: Path) -> None:
    """NUL-delimited rename counts attach to the new repository path.

    Git emits old and new names after one numeric record. The parser must consume
    both while counting additions and removals once.
    """
    repository = GitBackend(tmp_path)

    counts = repository._parse_numstat_output(
        b"2\t1\t\0old/name.txt\0new/name.txt\0"
    )

    assert counts == ({"new/name.txt": (2, 1)}, 2, 1)


def test_batch_loads_literal_paths_without_argv_pathspecs(
    tmp_path: Path,
) -> None:
    """Load exact tree/index names, arbitrary bytes, and per-File misses.

    Repository reads must preserve byte content and path identity while containing
    an absent File to that individual lookup.
    """
    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    magic_name = ":(glob)literal.txt"
    newline_name = "line\nbreak.bin"
    stage_name = "0:notes.txt"
    (tmp_path / magic_name).write_bytes(b"committed magic\n")
    (tmp_path / newline_name).write_bytes(b"\x00committed\nbytes\xff")
    subprocess.run(
        ["git", "add", "--all"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "literal paths"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / magic_name).write_bytes(b"staged magic\n")
    (tmp_path / stage_name).write_bytes(b"literal stage-shaped name\n")
    subprocess.run(
        ["git", "add", "--all"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    commit_id = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{commit_id},linked",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    repository = GitBackend.discover(cwd=tmp_path)
    assert repository.load_version(stage_name, "index") == (
        b"literal stage-shaped name\n"
    )
    # The single-File loader must reject non-blob objects exactly like the
    # batch: a gitlink resolves to a commit whose `git show` output would
    # otherwise masquerade as File content.
    with pytest.raises(DirdiffError):
        repository.load_version("linked", "index")
    loaded = repository.load_versions(
        (
            (magic_name, "HEAD"),
            (newline_name, "HEAD"),
            (magic_name, "index"),
            (stage_name, "index"),
            ("linked", "index"),
            ("missing\npath", "HEAD"),
        )
    )

    assert loaded[:4] == (
        b"committed magic\n",
        b"\x00committed\nbytes\xff",
        b"staged magic\n",
        b"literal stage-shaped name\n",
    )
    assert isinstance(loaded[4], DirdiffError)
    assert str(loaded[4]) == (
        "Git could not load :./linked: expected a File blob, got commit."
    )
    assert isinstance(loaded[5], DirdiffError)
    assert str(loaded[5]) == (
        "Git could not load HEAD:missing\npath: File is missing."
    )


def test_worktree_symlinks_load_as_git_records_them(tmp_path: Path) -> None:
    """Symlinks load as blobs holding the raw link target on every side.

    A worktree symlink pointing at a directory previously crashed loading
    because the reader followed the link; Git itself records only the target
    string. The old tree side, an untracked directory symlink, and a broken
    symlink must all load the exact target and compare against the committed
    target string.
    """
    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "real-dir").mkdir()
    (tmp_path / "real-dir" / "inner.txt").write_text("hi\n", encoding="utf-8")
    (tmp_path / "tracked-link").symlink_to("real-dir")
    subprocess.run(
        ["git", "add", "--all"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "tracked directory symlink"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    # Retarget the tracked link, add an untracked directory link and a
    # broken link: every one is ordinary blob content to Git.
    (tmp_path / "tracked-link").unlink()
    (tmp_path / "tracked-link").symlink_to("real-dir/inner.txt")
    (tmp_path / "untracked-link").symlink_to("real-dir")
    (tmp_path / "broken-link").symlink_to("no-such-target")

    backend = GitBackend.discover(cwd=tmp_path)
    diff = backend.repo_diff(left="HEAD", right="worktree", show_untracked=True)
    listed = {entry.right_path for entry in diff.paths}
    assert {"tracked-link", "untracked-link", "broken-link"} <= listed

    assert backend.load_version("tracked-link", "HEAD") == b"real-dir"
    assert (
        backend.load_version("tracked-link", "worktree")
        == b"real-dir/inner.txt"
    )
    assert backend.load_version("untracked-link", "worktree") == b"real-dir"
    assert backend.load_version("broken-link", "worktree") == b"no-such-target"
    batch = backend.load_versions(
        (
            ("tracked-link", "HEAD"),
            ("tracked-link", "worktree"),
            ("untracked-link", "worktree"),
            ("broken-link", "worktree"),
        )
    )
    assert batch == (
        b"real-dir",
        b"real-dir/inner.txt",
        b"real-dir",
        b"no-such-target",
    )


def test_lfs_pointers_load_as_their_objects_from_the_local_store(
    tmp_path: Path,
) -> None:
    """Load local LFS payloads without changing Git identities or raw links.

    Real committed pointers exercise single and batch loading on tree, index,
    and worktree sides. Missing, wrong-sized, or unreadable objects and pointers
    with extensions produce ordinary loading errors. Pointer-like text and symlinks
    stay raw. Storage configuration applies to existing backend instances.
    No Git LFS installation, network access, or filters are required.
    """
    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    held = b"\x89PNG held object bytes\n" * 3
    held_oid = hashlib.sha256(held).hexdigest()
    absent_oid = "b" * 64
    (tmp_path / "held.png").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\n"
        + f"oid sha256:{held_oid}\nsize {len(held)}\n".encode()
    )
    (tmp_path / "absent.bin").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\n"
        + f"oid sha256:{absent_oid}\nsize 12345\n".encode()
    )
    # Keys out of alphabetical order: valid-looking but not a pointer.
    (tmp_path / "lookalike.txt").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\n"
        + b"size 5\noid sha256:"
        + b"c" * 64
        + b"\n"
    )
    pointer_text = (tmp_path / "held.png").read_bytes()
    (tmp_path / "documentation.txt").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\n"
        b"This describes the pointer format; it is not a pointer.\n"
    )
    # A link whose target spells the held pointer, byte for byte.
    os.symlink(os.fsdecode(pointer_text), tmp_path / "pointer-link")
    (tmp_path / "extended.bin").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\n"
        + b"ext-0-encrypt sha256:"
        + b"e" * 64
        + f"\noid sha256:{held_oid}\nsize {len(held)}\n".encode()
    )
    subprocess.run(
        ["git", "add", "--all"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "pointers"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    store_dir = tmp_path / ".git" / "lfs" / "objects"
    (store_dir / held_oid[:2] / held_oid[2:4]).mkdir(parents=True)
    (store_dir / held_oid[:2] / held_oid[2:4] / held_oid).write_bytes(held)
    backend = GitBackend.discover(cwd=tmp_path)
    for side in ("HEAD", "index", "worktree"):
        assert backend.load_version("held.png", side) == held
        assert backend.file_size("held.png", side) == len(held)
        assert backend.load_versions((("held.png", side),)) == (held,)
        for name in ("lookalike.txt", "documentation.txt", "pointer-link"):
            expected = (
                pointer_text
                if name == "pointer-link"
                else (tmp_path / name).read_bytes()
            )
            assert backend.load_version(name, side) == expected
            assert backend.file_size(name, side) == len(expected)
            assert backend.load_versions(((name, side),)) == (expected,)
    # Retarget the worktree pointer at an object the store lacks, as a fresh
    # checkout with `GIT_LFS_SKIP_SMUDGE=1` leaves it.
    (tmp_path / "held.png").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\n"
        + b"oid sha256:"
        + b"d" * 64
        + b"\nsize 7\n"
    )

    empty_tree = (
        subprocess.run(
            ["git", "hash-object", "-t", "tree", "--stdin"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            input=b"",
        )
        .stdout.decode()
        .strip()
    )
    diff = backend.repo_diff(left=empty_tree, right="HEAD")
    identities = {
        entry.right_path: entry.right_object_id for entry in diff.paths
    }
    for name in (
        "held.png",
        "absent.bin",
        "lookalike.txt",
        "pointer-link",
        "extended.bin",
    ):
        blob_id = (
            subprocess.run(
                ["git", "rev-parse", f"HEAD:{name}"],
                cwd=tmp_path,
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .strip()
        )
        assert identities[name] == blob_id, name
    assert identities["held.png"] != held_oid
    assert identities["pointer-link"] == identities["held.png"]

    assert backend.file_size("absent.bin", "HEAD") == 12345
    assert backend.file_size("held.png", "worktree") == 7
    with pytest.raises(
        DirdiffError, match="Unsupported Git LFS pointer version or extensions"
    ):
        backend.load_version("extended.bin", "HEAD")
    with pytest.raises(DirdiffError) as absent_failure:
        backend.load_version("absent.bin", "HEAD")
    assert f"object {absent_oid} (12345 bytes)" in str(absent_failure.value)
    assert "`git lfs fetch <remote> HEAD`" in str(absent_failure.value)

    batch = backend.load_versions(
        (
            ("held.png", "HEAD"),
            ("absent.bin", "HEAD"),
            ("held.png", "worktree"),
            ("lookalike.txt", "index"),
            ("pointer-link", "HEAD"),
            ("pointer-link", "worktree"),
            ("extended.bin", "index"),
        )
    )
    assert batch[0] == held
    assert isinstance(batch[1], DirdiffError)
    assert "HEAD:absent.bin points to Git LFS object" in str(batch[1])
    assert isinstance(batch[2], DirdiffError)
    assert "worktree:held.png points to Git LFS object" in str(batch[2])
    assert "`git lfs fetch <remote>`" in str(batch[2])
    assert batch[3] == (tmp_path / "lookalike.txt").read_bytes()
    assert batch[4] == pointer_text
    assert batch[5] == pointer_text
    assert isinstance(batch[6], DirdiffError)
    assert "Unsupported Git LFS pointer version or extensions" in str(batch[6])

    # A damaged or unreadable store entry is refused at load; the pointer's
    # identity is unaffected.
    held_path = store_dir / held_oid[:2] / held_oid[2:4] / held_oid
    for wrong_size in (held[:-1], held + b"\0"):
        held_path.write_bytes(wrong_size)
        with pytest.raises(
            DirdiffError,
            match=f"size mismatch, expected {len(held)} bytes",
        ):
            backend.load_version("held.png", "HEAD")
    # Same-sized store contents are trusted, without another digest pass.
    held_path.write_bytes(b"x" * len(held))
    assert backend.load_version("held.png", "HEAD") == b"x" * len(held)
    held_path.write_bytes(held)
    # Root bypasses Unix read permissions; the other cases still run there.
    if os.geteuid() != 0:
        held_path.chmod(0)
        try:
            with pytest.raises(
                DirdiffError, match="Could not load Git LFS object"
            ):
                backend.load_version("held.png", "HEAD")
        finally:
            held_path.chmod(0o644)

    subprocess.run(
        ["git", "config", "lfs.storage", ""],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert backend.load_version("held.png", "HEAD") == held
    subprocess.run(
        ["git", "config", "lfs.storage", "elsewhere"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    with pytest.raises(DirdiffError, match="missing from local storage"):
        backend.load_version("held.png", "HEAD")

    relocated = (
        tmp_path
        / ".git"
        / "elsewhere"
        / "objects"
        / held_oid[:2]
        / held_oid[2:4]
        / held_oid
    )
    relocated.parent.mkdir(parents=True)
    held_path.rename(relocated)
    assert backend.load_version("held.png", "HEAD") == held

    # Absolute storage and a linked worktree must find the same local object.
    subprocess.run(
        ["git", "config", "lfs.storage", str(relocated.parents[3])],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert backend.load_version("held.png", "index") == held
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(linked), "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    linked_backend = GitBackend.discover(cwd=linked)
    for side in ("HEAD", "index", "worktree"):
        assert linked_backend.load_version("held.png", side) == held
        assert linked_backend.file_size("held.png", side) == len(held)
        assert linked_backend.load_versions((("held.png", side),)) == (held,)

    # Restore default storage to exercise the shared common directory too.
    relocated.rename(held_path)
    subprocess.run(
        ["git", "config", "--unset", "lfs.storage"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert linked_backend.load_version("held.png", "HEAD") == held
