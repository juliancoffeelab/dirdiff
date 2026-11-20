from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Boolean, Engine, ForeignKey, String, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from dirdiff.db.base import TableBase

__all__ = [
    "PreferencesStore",
]


class UserPreferences(TableBase):
    __tablename__ = "user_preferences"

    user_profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile.id"),
        primary_key=True,
    )

    aggressive_folds: Mapped[bool] = mapped_column(Boolean, nullable=False)

    git_algorithm: Mapped[str] = mapped_column(String, nullable=False)

    git_ignore_space_change: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )

    git_word_diff: Mapped[bool] = mapped_column(Boolean, nullable=False)

    git_word_boundary: Mapped[str] = mapped_column(String, nullable=False)

    git_word_diff_regex: Mapped[str | None] = mapped_column(
        String, nullable=True
    )

    difftastic_ignore_comments: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )

    git_color_moved: Mapped[str] = mapped_column(String, nullable=False)

    git_color_moved_ws: Mapped[str] = mapped_column(String, nullable=False)

    gumtree_matcher: Mapped[str] = mapped_column(String, nullable=False)


@dataclass(frozen=True)
class PreferencesRecord:
    user_profile_id: int

    aggressive_folds: bool

    git_algorithm: str

    git_ignore_space_change: bool

    git_word_diff: bool

    git_word_boundary: str

    git_word_diff_regex: str | None

    difftastic_ignore_comments: bool

    git_color_moved: str

    git_color_moved_ws: str

    gumtree_matcher: str


class PreferencesStore:
    def __init__(self, engine: Engine) -> None:
        self.engine: Engine = engine

    def get(self, user_profile_id: int) -> PreferencesRecord | None:
        with Session(self.engine) as session:
            row = session.execute(
                select(
                    UserPreferences.user_profile_id,
                    UserPreferences.aggressive_folds,
                    UserPreferences.git_algorithm,
                    UserPreferences.git_ignore_space_change,
                    UserPreferences.git_word_diff,
                    UserPreferences.git_word_boundary,
                    UserPreferences.git_word_diff_regex,
                    UserPreferences.difftastic_ignore_comments,
                    UserPreferences.gumtree_matcher,
                    UserPreferences.git_color_moved,
                    UserPreferences.git_color_moved_ws,
                ).where(UserPreferences.user_profile_id == user_profile_id)
            ).one_or_none()
            if row is None:
                return None
            return PreferencesRecord(
                user_profile_id=row[0],
                aggressive_folds=row[1],
                git_algorithm=row[2],
                git_ignore_space_change=row[3],
                git_word_diff=row[4],
                git_word_boundary=row[5],
                git_word_diff_regex=row[6],
                difftastic_ignore_comments=row[7],
                gumtree_matcher=row[8],
                git_color_moved=row[9],
                git_color_moved_ws=row[10],
            )

    def get_or_create(self, user_profile_id: int) -> PreferencesRecord:
        # TODO: make get_or_create return None for a user that doesn't exist,
        # so that callers dont need to validate it to produce a useful error
        with Session(self.engine) as session, session.begin():
            # NOTE: we use sqlite upcert with conflict mechanic here to
            # do the work in one atomic query
            row = session.execute(
                sqlite_insert(UserPreferences)
                .values(
                    user_profile_id=user_profile_id,
                    aggressive_folds=True,
                    git_algorithm="myers",
                    git_ignore_space_change=False,
                    git_word_diff=False,
                    git_word_boundary="default",
                    git_word_diff_regex=None,
                    difftastic_ignore_comments=False,
                    gumtree_matcher="gumtree-simple",
                    git_color_moved="no",
                    git_color_moved_ws="no",
                )
                .on_conflict_do_update(
                    index_elements=[UserPreferences.user_profile_id],
                    # on conflict, set `aggressive_folds` back to old row
                    # instead of writing down the default `True`
                    set_={
                        "aggressive_folds": UserPreferences.aggressive_folds,
                    },
                )
                .returning(
                    UserPreferences.user_profile_id,
                    UserPreferences.aggressive_folds,
                    UserPreferences.git_algorithm,
                    UserPreferences.git_ignore_space_change,
                    UserPreferences.git_word_diff,
                    UserPreferences.git_word_boundary,
                    UserPreferences.git_word_diff_regex,
                    UserPreferences.difftastic_ignore_comments,
                    UserPreferences.gumtree_matcher,
                    UserPreferences.git_color_moved,
                    UserPreferences.git_color_moved_ws,
                )
            ).one()
            return PreferencesRecord(
                user_profile_id=row[0],
                aggressive_folds=row[1],
                git_algorithm=row[2],
                git_ignore_space_change=row[3],
                git_word_diff=row[4],
                git_word_boundary=row[5],
                git_word_diff_regex=row[6],
                difftastic_ignore_comments=row[7],
                gumtree_matcher=row[8],
                git_color_moved=row[9],
                git_color_moved_ws=row[10],
            )

    def save(
        self,
        user_profile_id: int,
        aggressive_folds: bool,
        git_algorithm: str,
        git_ignore_space_change: bool,
        git_word_diff: bool,
        git_word_boundary: str,
        git_word_diff_regex: str | None,
        difftastic_ignore_comments: bool,
        gumtree_matcher: str,
        git_color_moved: str,
        git_color_moved_ws: str,
    ) -> PreferencesRecord:
        with Session(self.engine) as session, session.begin():
            row = session.execute(
                sqlite_insert(UserPreferences)
                .values(
                    user_profile_id=user_profile_id,
                    aggressive_folds=aggressive_folds,
                    git_algorithm=git_algorithm,
                    git_ignore_space_change=git_ignore_space_change,
                    git_word_diff=git_word_diff,
                    git_word_boundary=git_word_boundary,
                    git_word_diff_regex=git_word_diff_regex,
                    difftastic_ignore_comments=difftastic_ignore_comments,
                    gumtree_matcher=gumtree_matcher,
                    git_color_moved=git_color_moved,
                    git_color_moved_ws=git_color_moved_ws,
                )
                .on_conflict_do_update(
                    index_elements=[UserPreferences.user_profile_id],
                    set_={
                        "aggressive_folds": aggressive_folds,
                        "git_algorithm": git_algorithm,
                        "git_ignore_space_change": git_ignore_space_change,
                        "git_word_diff": git_word_diff,
                        "git_word_boundary": git_word_boundary,
                        "git_word_diff_regex": git_word_diff_regex,
                        "difftastic_ignore_comments": difftastic_ignore_comments,
                        "gumtree_matcher": gumtree_matcher,
                        "git_color_moved": git_color_moved,
                        "git_color_moved_ws": git_color_moved_ws,
                    },
                )
                .returning(
                    UserPreferences.user_profile_id,
                    UserPreferences.aggressive_folds,
                    UserPreferences.git_algorithm,
                    UserPreferences.git_ignore_space_change,
                    UserPreferences.git_word_diff,
                    UserPreferences.git_word_boundary,
                    UserPreferences.git_word_diff_regex,
                    UserPreferences.difftastic_ignore_comments,
                    UserPreferences.gumtree_matcher,
                    UserPreferences.git_color_moved,
                    UserPreferences.git_color_moved_ws,
                )
            ).one()
            return PreferencesRecord(
                user_profile_id=row[0],
                aggressive_folds=row[1],
                git_algorithm=row[2],
                git_ignore_space_change=row[3],
                git_word_diff=row[4],
                git_word_boundary=row[5],
                git_word_diff_regex=row[6],
                difftastic_ignore_comments=row[7],
                gumtree_matcher=row[8],
                git_color_moved=row[9],
                git_color_moved_ws=row[10],
            )
