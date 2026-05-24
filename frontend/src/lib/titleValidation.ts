/**
 * Client-side mirror of the backend title validation rules.
 *
 * The backend (`backend/services/title_validator.py`) is the source of
 * truth — this module exists so the UI can give immediate inline
 * feedback that matches what the server will say. Error codes match the
 * backend `TitleErrorCode` enum so the same `code` strings flow from
 * either side without translation.
 *
 * Rules (mirrors Requirement 4 in `.kiro/specs/project-titles/requirements.md`):
 *   1. Trim leading/trailing whitespace.
 *   2. Empty after trim → `title_empty`.
 *   3. Trimmed length, measured in Unicode code points, > 100 → `title_too_long`.
 *   4. Any character whose Unicode category is `Cc` → `title_control_chars`.
 *
 * Length is counted in code points (`Array.from(s).length`) rather than
 * UTF-16 code units (`s.length`) so CJK and astral-plane characters
 * count as one each, matching the backend's `len(str)`.
 */

export const MAX_TITLE_LENGTH = 100;

/**
 * Error codes returned by `validateTitleShape`. Values match the
 * backend `TitleErrorCode` enum (`backend/services/title_validator.py`).
 *
 * `title_required` is reserved for the case where the candidate is not
 * a string at all (e.g. the field was never filled in). `title_empty`
 * covers the case where the user typed only whitespace.
 *
 * `title_duplicate` is excluded here because uniqueness can only be
 * checked on the server, not from the client.
 */
export type TitleErrorCode =
  | "title_required"
  | "title_empty"
  | "title_too_long"
  | "title_control_chars";

export type TitleValidationResult =
  | { ok: true; trimmed: string }
  | { ok: false; code: TitleErrorCode };

/** Matches any character in Unicode general category `Cc` (control). */
const CONTROL_CHAR_RE = /\p{Cc}/u;

/**
 * Validate a candidate title against the shape rules.
 *
 * Returns the trimmed value on success, or an error code matching the
 * backend `TitleErrorCode` on failure. Uniqueness (`title_duplicate`)
 * is intentionally not checked here — that is server-only.
 */
export function validateTitleShape(
  candidate: string,
): TitleValidationResult {
  if (typeof candidate !== "string") {
    return { ok: false, code: "title_required" };
  }
  const trimmed = candidate.trim();
  if (trimmed.length === 0) {
    return { ok: false, code: "title_empty" };
  }
  // Count code points, not UTF-16 code units, to match the backend.
  const codePointLength = Array.from(trimmed).length;
  if (codePointLength > MAX_TITLE_LENGTH) {
    return { ok: false, code: "title_too_long" };
  }
  if (CONTROL_CHAR_RE.test(trimmed)) {
    return { ok: false, code: "title_control_chars" };
  }
  return { ok: true, trimmed };
}
