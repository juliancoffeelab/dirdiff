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

from dirdiff.change_source import GitChangeSource, GitRepository
from dirdiff.engines import DirdiffError
from dirdiff.util import Some


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

    head_id = GitRepository(tmp_path).commit_id("HEAD")
    paths = (
        GitChangeSource(
            tmp_path,
            left=head_id,
            right="worktree",
            show_untracked=False,
            max_link_target_bytes=1024 * 1024,
        )
        .changes()
        .files
    )

    assert len(paths) == 1
    assert paths[0].display_name == "alpha.txt -> renamed.txt"
    assert paths[0].change_type == "rename"
    assert (
        Some.present(paths[0].left)
        and paths[0].left.value.key.path == "alpha.txt"
    )
    assert (
        Some.present(paths[0].right)
        and paths[0].right.value.key.path == "renamed.txt"
    )


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

    service = TextDiffService(GitRepository.discover(cwd=tmp_path))
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

    service = TextDiffService(GitRepository.discover(cwd=tmp_path))

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
    repository = GitRepository(tmp_path)

    counts = repository._workspace._parse_numstat_output(
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

    repository = GitRepository.discover(cwd=tmp_path)
    assert repository._workspace.load_version(stage_name, "index") == (
        b"literal stage-shaped name\n"
    )
    # A gitlink has no blob; both loaders return the referenced commit id as
    # the side's content, here for a commit that is present locally.
    assert repository._workspace.load_version("linked", "index") == (
        f"{commit_id}\n".encode()
    )
    loaded = repository._workspace.load_versions(
        (
            (magic_name, "HEAD", None, None),
            (newline_name, "HEAD", None, None),
            (magic_name, "index", None, None),
            (stage_name, "index", None, None),
            ("linked", "index", None, None),
            ("missing\npath", "HEAD", None, None),
        )
    )

    assert loaded[:4] == (
        b"committed magic\n",
        b"\x00committed\nbytes\xff",
        b"staged magic\n",
        b"literal stage-shaped name\n",
    )
    assert loaded[4] == f"{commit_id}\n".encode()
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

    backend = GitRepository.discover(cwd=tmp_path)
    head_id = backend.commit_id("HEAD")
    changes = GitChangeSource(
        tmp_path,
        left=head_id,
        right="worktree",
        show_untracked=True,
        max_link_target_bytes=1024 * 1024,
    ).changes()
    listed = {
        entry.right.get_map(lambda side: side.key.path)
        for entry in changes.files
    }
    assert {"tracked-link", "untracked-link", "broken-link"} <= listed

    assert (
        backend._workspace.load_version("tracked-link", "HEAD") == b"real-dir"
    )
    assert (
        backend._workspace.load_version("tracked-link", "worktree")
        == b"real-dir/inner.txt"
    )
    assert (
        backend._workspace.load_version("untracked-link", "worktree")
        == b"real-dir"
    )
    assert (
        backend._workspace.load_version("broken-link", "worktree")
        == b"no-such-target"
    )
    batch = backend._workspace.load_versions(
        (
            ("tracked-link", "HEAD", None, None),
            ("tracked-link", "worktree", None, None),
            ("untracked-link", "worktree", None, None),
            ("broken-link", "worktree", None, None),
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
    backend = GitRepository.discover(cwd=tmp_path)
    for side in ("HEAD", "index", "worktree"):
        assert backend._workspace.load_version("held.png", side) == held
        assert backend._workspace.file_size("held.png", side) == len(held)
        assert backend._workspace.load_versions(
            (("held.png", side, None, None),)
        ) == (held,)
        for name in ("lookalike.txt", "documentation.txt", "pointer-link"):
            expected = (
                pointer_text
                if name == "pointer-link"
                else (tmp_path / name).read_bytes()
            )
            assert backend._workspace.load_version(name, side) == expected
            assert backend._workspace.file_size(name, side) == len(expected)
            assert backend._workspace.load_versions(
                ((name, side, None, None),)
            ) == (expected,)
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
    changes = backend._workspace.list_changes(
        left=empty_tree, right=backend.commit_id("HEAD"), show_untracked=False
    )
    identities = {
        entry.right_path: entry.right_object_id
        for entry in changes.paths
        if entry.right_path is not None
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

    assert backend._workspace.file_size("absent.bin", "HEAD") == 12345
    assert backend._workspace.file_size("held.png", "worktree") == 7
    with pytest.raises(
        DirdiffError, match="Unsupported Git LFS pointer version or extensions"
    ):
        backend._workspace.load_version("extended.bin", "HEAD")
    with pytest.raises(DirdiffError) as absent_failure:
        backend._workspace.load_version("absent.bin", "HEAD")
    assert f"object {absent_oid} (12345 bytes)" in str(absent_failure.value)
    assert "`git lfs fetch <remote> HEAD`" in str(absent_failure.value)

    batch = backend._workspace.load_versions(
        (
            ("held.png", "HEAD", None, None),
            ("absent.bin", "HEAD", None, None),
            ("held.png", "worktree", None, None),
            ("lookalike.txt", "index", None, None),
            ("pointer-link", "HEAD", None, None),
            ("pointer-link", "worktree", None, None),
            ("extended.bin", "index", None, None),
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
            backend._workspace.load_version("held.png", "HEAD")
    # Same-sized store contents are trusted, without another digest pass.
    held_path.write_bytes(b"x" * len(held))
    assert backend._workspace.load_version("held.png", "HEAD") == b"x" * len(
        held
    )
    held_path.write_bytes(held)
    # Root bypasses Unix read permissions; the other cases still run there.
    if os.geteuid() != 0:
        held_path.chmod(0)
        try:
            with pytest.raises(
                DirdiffError, match="Could not load Git LFS object"
            ):
                backend._workspace.load_version("held.png", "HEAD")
        finally:
            held_path.chmod(0o644)

    subprocess.run(
        ["git", "config", "lfs.storage", ""],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert backend._workspace.load_version("held.png", "HEAD") == held
    subprocess.run(
        ["git", "config", "lfs.storage", "elsewhere"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    with pytest.raises(DirdiffError, match="missing from local storage"):
        backend._workspace.load_version("held.png", "HEAD")

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
    assert backend._workspace.load_version("held.png", "HEAD") == held

    # Absolute storage and a linked worktree must find the same local object.
    subprocess.run(
        ["git", "config", "lfs.storage", str(relocated.parents[3])],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert backend._workspace.load_version("held.png", "index") == held
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(linked), "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    linked_backend = GitRepository.discover(cwd=linked)
    for side in ("HEAD", "index", "worktree"):
        assert linked_backend._workspace.load_version("held.png", side) == held
        assert linked_backend._workspace.file_size("held.png", side) == len(
            held
        )
        assert linked_backend._workspace.load_versions(
            (("held.png", side, None, None),)
        ) == (held,)

    # Restore default storage to exercise the shared common directory too.
    relocated.rename(held_path)
    subprocess.run(
        ["git", "config", "--unset", "lfs.storage"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert linked_backend._workspace.load_version("held.png", "HEAD") == held


def test_submodule_sides_load_as_their_commit_ids(tmp_path: Path) -> None:
    """A submodule lists with mode 160000 and each side loads one commit id.

    The submodule's commits live only under `.git/modules`, so tree and index
    sides cannot be read as objects of the outer repository. Moving the
    submodule to another commit changes the worktree side; staging that move
    changes the index side; content changes inside the submodule alone are not
    an outer change.
    """
    inner = tmp_path / "inner"
    outer = tmp_path / "outer"
    inner.mkdir()
    outer.mkdir()
    for cwd, args in (
        (inner, ["init", "-q"]),
        (inner, ["config", "user.name", "Test User"]),
        (inner, ["config", "user.email", "test@example.com"]),
        (inner, ["commit", "-q", "--allow-empty", "-m", "first"]),
        (inner, ["commit", "-q", "--allow-empty", "-m", "second"]),
        (outer, ["init", "-q"]),
        (outer, ["config", "user.name", "Test User"]),
        (outer, ["config", "user.email", "test@example.com"]),
        # The nested clone ignores the outer repository configuration, so the
        # file transport must be allowed on the command itself.
        (
            outer,
            [
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                "-q",
                str(inner),
                "sub",
            ],
        ),
        (outer, ["commit", "-q", "-m", "submodule"]),
        (outer / "sub", ["checkout", "-q", "HEAD~1"]),
    ):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
    first, second = (
        subprocess.run(
            ["git", "rev-parse", ref],
            cwd=inner,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
        for ref in ("HEAD~1", "HEAD")
    )
    (outer / "sub" / "scratch.txt").write_text("dirty\n", encoding="utf-8")

    backend = GitRepository.discover(cwd=outer)
    (moved,) = backend._workspace.list_changes(
        left="HEAD", right="worktree", show_untracked=True
    ).paths
    assert (moved.left_path, moved.right_path) == ("sub", "sub")
    assert moved.change_type == "modify"
    assert (
        moved.left_properties is not None and moved.right_properties is not None
    )
    assert (moved.left_properties.kind, moved.right_properties.kind) == (
        "repository_reference",
        "repository_reference",
    )
    assert moved.left_object_id == second
    for side in ("HEAD", "index", "worktree"):
        assert backend._workspace.file_mode("sub", side) == "160000"
    assert backend._workspace.load_versions(
        (
            ("sub", "HEAD", None, None),
            ("sub", "index", None, None),
            ("sub", "worktree", None, None),
        )
    ) == (
        f"{second}\n".encode(),
        f"{second}\n".encode(),
        f"{first}\n".encode(),
    )
    assert (
        backend._workspace.load_version("sub", "worktree")
        == f"{first}\n".encode()
    )
    # Size inspection precedes link-target loading, so a gitlink must report
    # its materialized size, the commit id line, on every side.
    for side in ("HEAD", "index", "worktree"):
        assert backend._workspace.file_size("sub", side) == len(second) + 1

    subprocess.run(
        ["git", "add", "sub"], cwd=outer, check=True, capture_output=True
    )
    (staged,) = backend._workspace.list_changes(
        left="HEAD", right="index"
    ).paths
    assert (staged.left_object_id, staged.right_object_id) == (second, first)
    assert (
        backend._workspace.load_version("sub", "index") == f"{first}\n".encode()
    )
    assert (
        backend._workspace.list_changes(left="index", right="worktree").paths
        == ()
    )

    # Back on the committed revision, the leftover file inside the submodule
    # still makes it dirty, which is not an outer change.
    subprocess.run(
        ["git", "checkout", "-q", second],
        cwd=outer / "sub",
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "sub"], cwd=outer, check=True, capture_output=True
    )
    assert (outer / "sub" / "scratch.txt").exists()
    assert (
        backend._workspace.list_changes(
            left="HEAD", right="worktree", show_untracked=True
        ).paths
        == ()
    )


def test_submodule_replaced_by_a_regular_file_loads_both_sides(
    tmp_path: Path,
) -> None:
    """A path that was a submodule and became a file changes mode and content.

    The tree side is a gitlink whose commit the outer repository does not have,
    the worktree side is an ordinary file, and both load through one batch.
    """
    inner = tmp_path / "inner"
    outer = tmp_path / "outer"
    inner.mkdir()
    outer.mkdir()
    for cwd, args in (
        (inner, ["init", "-q"]),
        (inner, ["config", "user.name", "Test User"]),
        (inner, ["config", "user.email", "test@example.com"]),
        (inner, ["commit", "-q", "--allow-empty", "-m", "only"]),
        (outer, ["init", "-q"]),
        (outer, ["config", "user.name", "Test User"]),
        (outer, ["config", "user.email", "test@example.com"]),
        (
            outer,
            [
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                "-q",
                str(inner),
                "gone",
            ],
        ),
        (outer, ["commit", "-q", "-m", "submodule"]),
        (outer, ["rm", "-q", "-f", "gone"]),
        (outer, ["reset", "-q", "HEAD", "gone"]),
    ):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
    inner_id = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=inner,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )
    (outer / "gone").write_text("plain file now\n", encoding="utf-8")

    backend = GitRepository.discover(cwd=outer)
    entries = {
        entry.right_path: entry
        for entry in backend._workspace.list_changes(
            left="HEAD", right="worktree"
        ).paths
    }
    # Removing the submodule also rewrote `.gitmodules`.
    assert set(entries) == {"gone", ".gitmodules"}
    replaced = entries["gone"]
    assert replaced.left_path == "gone"
    assert (
        replaced.left_properties is not None
        and replaced.right_properties is not None
    )
    assert (replaced.left_properties.kind, replaced.right_properties.kind) == (
        "repository_reference",
        "file",
    )
    assert backend._workspace.file_mode("gone", "HEAD") == "160000"
    assert backend._workspace.file_mode("gone", "worktree") == "100644"
    assert backend._workspace.load_versions(
        (("gone", "HEAD", None, None), ("gone", "worktree", None, None))
    ) == (
        f"{inner_id}\n".encode(),
        b"plain file now\n",
    )


def test_untracked_embedded_repositories_list_as_gitlinks(
    tmp_path: Path,
) -> None:
    """Untracked directories holding a repository are one gitlink entry each.

    Git lists them with a trailing slash instead of descending into them. Such
    an entry loads as the embedded HEAD commit id, and an embedded repository
    without any commit fails to load, exactly as `git add` refuses it. An
    ordinary untracked directory still lists by its files.
    """
    for cwd, args in (
        (tmp_path, ["init", "-q"]),
        (tmp_path, ["config", "user.name", "Test User"]),
        (tmp_path, ["config", "user.email", "test@example.com"]),
        (tmp_path, ["commit", "-q", "--allow-empty", "-m", "root"]),
        (tmp_path, ["init", "-q", "embedded"]),
        (tmp_path / "embedded", ["config", "user.name", "Test User"]),
        (tmp_path / "embedded", ["config", "user.email", "test@example.com"]),
        (tmp_path / "embedded", ["commit", "-q", "--allow-empty", "-m", "e"]),
        (tmp_path, ["init", "-q", "hollow"]),
    ):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
    embedded_id = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path / "embedded",
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )
    (tmp_path / "plain-dir").mkdir()
    (tmp_path / "plain-dir" / "note.txt").write_text("n\n", encoding="utf-8")

    backend = GitRepository.discover(cwd=tmp_path)
    entries = {
        entry.right_path: entry
        for entry in backend._workspace.list_changes(
            left="HEAD", right="worktree", show_untracked=True
        ).paths
    }
    assert set(entries) == {"embedded", "hollow", "plain-dir/note.txt"}
    assert all(entry.untracked for entry in entries.values())
    assert entries["embedded"].right_properties is not None
    assert entries["embedded"].right_properties.kind == "repository_reference"
    assert entries["hollow"].right_properties is not None
    assert entries["hollow"].right_properties.kind == "repository_reference"
    assert entries["plain-dir/note.txt"].right_properties is not None
    assert entries["plain-dir/note.txt"].right_properties.kind == "file"
    assert backend._workspace.file_mode("embedded", "worktree") == "160000"
    with pytest.raises(DirdiffError):
        backend._workspace.file_mode("plain-dir", "worktree")

    embedded, hollow, plain = backend._workspace.load_versions(
        (
            ("embedded", "worktree", None, None),
            ("hollow", "worktree", None, None),
            ("plain-dir", "worktree", None, None),
        )
    )
    assert embedded == f"{embedded_id}\n".encode()
    assert isinstance(hollow, DirdiffError)
    assert "hollow" in str(hollow) and "HEAD" in str(hollow)
    assert isinstance(plain, DirdiffError)
    assert backend._workspace.load_version("embedded", "worktree") == embedded
