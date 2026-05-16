import { describe, it, expect } from "vitest";
import * as fc from "fast-check";
import { getVisibleSubtitles } from "../components/VideoCanvas";
import type { SubtitleSegment } from "../types";

/**
 * Feature: story-video-editor, Property 2: Visible subtitles at time T
 * Validates: Requirements 2.3
 *
 * For any list of subtitle segments and any time value T, the set of subtitles
 * visible at time T should be exactly those segments where start_time <= T < end_time.
 */

/** Arbitrary for a single SubtitleSegment with valid timing (startTime < endTime, both >= 0). */
const subtitleSegmentArb: fc.Arbitrary<SubtitleSegment> = fc
  .record({
    startTime: fc.float({ min: 0, max: Math.fround(3600), noNaN: true }),
    duration: fc.float({ min: Math.fround(0.001), max: Math.fround(600), noNaN: true }),
  })
  .map(({ startTime, duration }) => ({
    id: crypto.randomUUID(),
    text: "测试字幕",
    startTime,
    endTime: startTime + duration,
    position: { x: 0.5, y: 0.85 },
    style: {
      fontSize: 0.047,
      fontColor: "#FFFFFF",
      outlineColor: "#000000",
      fontFamily: "Noto Sans CJK SC",
    },
  }));

const subtitleListArb = fc.array(subtitleSegmentArb, { minLength: 0, maxLength: 20 });

describe("Feature: story-video-editor, Property 2: Visible subtitles at time T", () => {
  it("should return exactly the subtitles where startTime <= T < endTime", () => {
    fc.assert(
      fc.property(
        subtitleListArb,
        fc.float({ min: 0, max: Math.fround(4200), noNaN: true }),
        (subtitles, time) => {
          const visible = getVisibleSubtitles(subtitles, time);

          // Expected: filter using the same half-open interval
          const expected = subtitles.filter(
            (s) => s.startTime <= time && time < s.endTime
          );

          // Same count
          expect(visible.length).toBe(expected.length);

          // Every visible subtitle must satisfy the interval
          for (const sub of visible) {
            expect(sub.startTime).toBeLessThanOrEqual(time);
            expect(time).toBeLessThan(sub.endTime);
          }

          // Every subtitle satisfying the interval must be in the result
          const visibleIds = new Set(visible.map((s) => s.id));
          for (const sub of expected) {
            expect(visibleIds.has(sub.id)).toBe(true);
          }
        }
      ),
      { numRuns: 100 }
    );
  });

  // Edge case: T exactly at startTime (should be visible)
  it("should include a subtitle when T equals startTime", () => {
    fc.assert(
      fc.property(subtitleSegmentArb, (sub) => {
        const visible = getVisibleSubtitles([sub], sub.startTime);
        expect(visible).toHaveLength(1);
        expect(visible[0].id).toBe(sub.id);
      }),
      { numRuns: 100 }
    );
  });

  // Edge case: T exactly at endTime (should NOT be visible — half-open interval)
  it("should exclude a subtitle when T equals endTime", () => {
    fc.assert(
      fc.property(subtitleSegmentArb, (sub) => {
        const visible = getVisibleSubtitles([sub], sub.endTime);
        expect(visible).toHaveLength(0);
      }),
      { numRuns: 100 }
    );
  });

  // Edge case: T between start and end (should be visible)
  it("should include a subtitle when T is between startTime and endTime", () => {
    fc.assert(
      fc.property(
        subtitleSegmentArb,
        fc.float({ min: 0, max: 1, noNaN: true, noDefaultInfinity: true }),
        (sub, fraction) => {
          const midTime = sub.startTime + fraction * (sub.endTime - sub.startTime);
          // midTime is in [startTime, endTime]; only exclude exact endTime
          if (midTime < sub.endTime) {
            const visible = getVisibleSubtitles([sub], midTime);
            expect(visible).toHaveLength(1);
            expect(visible[0].id).toBe(sub.id);
          }
        }
      ),
      { numRuns: 100 }
    );
  });

  // Edge case: empty subtitle list
  it("should return empty array for empty subtitle list", () => {
    fc.assert(
      fc.property(
        fc.float({ min: 0, max: Math.fround(4200), noNaN: true }),
        (time) => {
          const visible = getVisibleSubtitles([], time);
          expect(visible).toHaveLength(0);
        }
      ),
      { numRuns: 100 }
    );
  });

  // Edge case: multiple overlapping subtitles — all visible ones returned
  it("should return all overlapping subtitles visible at time T", () => {
    fc.assert(
      fc.property(
        fc.array(subtitleSegmentArb, { minLength: 2, maxLength: 10 }),
        (subtitles) => {
          // Pick a time that falls within the first subtitle's range
          const anchor = subtitles[0];
          const time = anchor.startTime;

          const visible = getVisibleSubtitles(subtitles, time);
          const expected = subtitles.filter(
            (s) => s.startTime <= time && time < s.endTime
          );

          expect(visible.length).toBe(expected.length);

          const visibleIds = new Set(visible.map((s) => s.id));
          for (const sub of expected) {
            expect(visibleIds.has(sub.id)).toBe(true);
          }
        }
      ),
      { numRuns: 100 }
    );
  });
});
