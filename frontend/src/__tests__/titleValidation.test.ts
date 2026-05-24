import { describe, it, expect } from "vitest";
import {
  MAX_TITLE_LENGTH,
  validateTitleShape,
} from "../lib/titleValidation";

describe("validateTitleShape", () => {
  it("exposes MAX_TITLE_LENGTH = 100", () => {
    expect(MAX_TITLE_LENGTH).toBe(100);
  });

  it("accepts a simple title and returns the trimmed value", () => {
    const result = validateTitleShape("Hello");
    expect(result).toEqual({ ok: true, trimmed: "Hello" });
  });

  it("trims leading and trailing whitespace", () => {
    const result = validateTitleShape("  Hello world  ");
    expect(result).toEqual({ ok: true, trimmed: "Hello world" });
  });

  it("preserves internal whitespace and Unicode (e.g. CJK)", () => {
    const result = validateTitleShape("  你好 世界  ");
    expect(result).toEqual({ ok: true, trimmed: "你好 世界" });
  });

  it("rejects an empty string with title_empty", () => {
    expect(validateTitleShape("")).toEqual({
      ok: false,
      code: "title_empty",
    });
  });

  it("rejects a whitespace-only string with title_empty", () => {
    expect(validateTitleShape("   \t  ")).toEqual({
      ok: false,
      code: "title_empty",
    });
  });

  it("accepts exactly 100 code points", () => {
    const exactly100 = "a".repeat(100);
    expect(validateTitleShape(exactly100)).toEqual({
      ok: true,
      trimmed: exactly100,
    });
  });

  it("rejects titles whose trimmed length exceeds 100 code points", () => {
    const oversize = "a".repeat(101);
    expect(validateTitleShape(oversize)).toEqual({
      ok: false,
      code: "title_too_long",
    });
  });

  it("counts Unicode code points, not UTF-16 code units, for length", () => {
    // 🎬 is a single code point but two UTF-16 code units.
    // 100 of them = 100 code points (valid), 200 code units (would be
    // rejected by string.length).
    const movieClapper = "🎬".repeat(100);
    expect(movieClapper.length).toBe(200); // sanity: UTF-16 code units
    expect(validateTitleShape(movieClapper)).toEqual({
      ok: true,
      trimmed: movieClapper,
    });

    const oversizeAstral = "🎬".repeat(101);
    expect(validateTitleShape(oversizeAstral)).toEqual({
      ok: false,
      code: "title_too_long",
    });
  });

  it("rejects titles containing control characters", () => {
    // U+0007 BELL is in category Cc.
    expect(validateTitleShape("Hello\u0007world")).toEqual({
      ok: false,
      code: "title_control_chars",
    });
    // U+0000 NULL is also Cc.
    expect(validateTitleShape("a\u0000b")).toEqual({
      ok: false,
      code: "title_control_chars",
    });
  });

  it("returns title_required when given a non-string", () => {
    // Defensive: catches misuse where a form wires up `undefined`.
    expect(
      validateTitleShape(undefined as unknown as string),
    ).toEqual({ ok: false, code: "title_required" });
  });
});
