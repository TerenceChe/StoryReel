"""Pure validation rules for project titles.

This module owns the trim/length/character-class rules (Requirement 4) and
the per-owner uniqueness comparison key (Requirement 3.1). Both
`validate_shape` and `check_uniqueness` are pure functions: they neither
read from storage nor mutate state. The service layer is responsible for
gathering the sibling list passed to `check_uniqueness`.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum

MAX_TITLE_LENGTH = 100


class TitleErrorCode(str, Enum):
    """Error codes for title validation failures.

    Mirrored on the frontend so that error responses can be rendered as
    field-targeted, code-driven messages rather than parsed strings.
    """

    REQUIRED = "title_required"
    EMPTY = "title_empty"
    TOO_LONG = "title_too_long"
    CONTROL_CHARS = "title_control_chars"
    DUPLICATE = "title_duplicate"


@dataclass
class TitleValidationError(Exception):
    """Raised when a candidate title fails any validation rule."""

    code: TitleErrorCode
    message: str


def normalize(candidate: str) -> str:
    """Trim leading and trailing whitespace. No other transformation."""
    return candidate.strip()


def validate_shape(candidate: str | None) -> str:
    """Apply Requirement 4 rules. Returns the trimmed title or raises.

    Length is measured in Unicode code points (`len(str)`), which matches
    user expectation for CJK content where each Han character is one code
    point.
    """
    if candidate is None:
        raise TitleValidationError(
            TitleErrorCode.REQUIRED, "Title is required."
        )
    trimmed = normalize(candidate)
    if len(trimmed) == 0:
        raise TitleValidationError(
            TitleErrorCode.EMPTY, "Title must not be empty."
        )
    if len(trimmed) > MAX_TITLE_LENGTH:
        raise TitleValidationError(
            TitleErrorCode.TOO_LONG,
            f"Title must be at most {MAX_TITLE_LENGTH} characters.",
        )
    for ch in trimmed:
        if unicodedata.category(ch) == "Cc":
            raise TitleValidationError(
                TitleErrorCode.CONTROL_CHARS,
                "Title must not contain control characters.",
            )
    return trimmed


def title_key(trimmed: str) -> str:
    """Comparison key for uniqueness: casefolded, trimmed.

    Callers are expected to pass an already-trimmed value. For Han
    characters this is a no-op since they have no case, which matches
    Requirement 3.1's intent.
    """
    return trimmed.casefold()


def check_uniqueness(
    trimmed: str,
    *,
    self_project_id: str | None,
    siblings: list[tuple[str, str]],  # (project_id, stored_title)
) -> None:
    """Raise DUPLICATE if any sibling shares the same title key, excluding self.

    `siblings` is the full set of (project_id, stored_title) pairs owned by
    the same Owner as the candidate. The project being renamed (identified
    by `self_project_id`) is skipped so a no-op rename always succeeds
    (Requirement 3.4). Stored titles are normalized before comparison so
    pre-existing titles with incidental whitespace still compare correctly.
    """
    candidate_key = title_key(trimmed)
    for pid, stored in siblings:
        if pid == self_project_id:
            continue
        if title_key(normalize(stored)) == candidate_key:
            raise TitleValidationError(
                TitleErrorCode.DUPLICATE,
                "A project with this title already exists.",
            )
