import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from helpers import TextDiffService

from dirdiff.change_source import GitChangeSource, GitRepository
from dirdiff.engines import DirdiffError
from dirdiff.util import Ok


def test_ref_choices_use_configured_remote_names_with_slashes(
    tmp_path: Path,
) -> None:
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


def test_batch_loads_literal_paths_without_argv_pathspecs(
    tmp_path: Path,
) -> None:
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

    assert tuple(result.or_raise() for result in loaded[:4]) == (
        b"committed magic\n",
        b"\x00committed\nbytes\xff",
        b"staged magic\n",
        b"literal stage-shaped name\n",
    )
    assert loaded[4].or_raise() == f"{commit_id}\n".encode()
    assert not Ok.good(loaded[5])
    assert str(loaded[5].error) == (
        "Git could not load HEAD:missing\npath: File is missing."
    )


def test_worktree_symlinks_load_as_git_records_them(tmp_path: Path) -> None:
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
    assert tuple(result.or_raise() for result in batch) == (
        b"real-dir",
        b"real-dir/inner.txt",
        b"real-dir",
        b"no-such-target",
    )


def test_lfs_pointers_load_as_their_objects_from_the_local_store(
    tmp_path: Path,
) -> None:
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
        ) == (Ok(held),)
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
            ) == (Ok(expected),)
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
    assert batch[0].or_raise() == held
    assert not Ok.good(batch[1])
    assert "HEAD:absent.bin points to Git LFS object" in str(batch[1].error)
    assert not Ok.good(batch[2])
    assert "worktree:held.png points to Git LFS object" in str(batch[2].error)
    assert "`git lfs fetch <remote>`" in str(batch[2].error)
    assert batch[3].or_raise() == (tmp_path / "lookalike.txt").read_bytes()
    assert batch[4].or_raise() == pointer_text
    assert batch[5].or_raise() == pointer_text
    assert not Ok.good(batch[6])
    assert "Unsupported Git LFS pointer version or extensions" in str(
        batch[6].error
    )

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
        ) == (Ok(held),)

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
        Ok(f"{second}\n".encode()),
        Ok(f"{second}\n".encode()),
        Ok(f"{first}\n".encode()),
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
