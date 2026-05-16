import { describe, it, expect } from "vitest";
import * as fc from "fast-check";
import { clampPosition } from "../components/VideoCanvas";

/**
 * Feature: story-video-editor, Property 3: Position clamping within bounds
 * Validates: Requirements 3.2
 */
describe("Feature: story-video-editor, Property 3: Position clamping within bounds", () => {
  it("clamped position keeps bounding box within [0,1] on both axes", () => {
    fc.assert(
      fc.property(
        fc.double({ min: -10, max: 10, noNaN: true, noDefaultInfinity: true }),  // x
        fc.double({ min: -10, max: 10, noNaN: true, noDefaultInfinity: true }),  // y
        fc.double({ min: 0.001, max: 1, noNaN: true, noDefaultInfinity: true }), // boxWidth
        fc.double({ min: 0.001, max: 1, noNaN: true, noDefaultInfinity: true }), // boxHeight
        (x, y, boxWidth, boxHeight) => {
          const clamped = clampPosition(x, y, boxWidth, boxHeight);

          // clamped_x >= 0
          expect(clamped.x).toBeGreaterThanOrEqual(0);
          // clamped_x + boxWidth <= 1
          expect(clamped.x + boxWidth).toBeLessThanOrEqual(1 + 1e-10);
          // clamped_y >= 0
          expect(clamped.y).toBeGreaterThanOrEqual(0);
          // clamped_y + boxHeight <= 1
          expect(clamped.y + boxHeight).toBeLessThanOrEqual(1 + 1e-10);
        },
      ),
      { numRuns: 200 },
    );
  });

  // Edge case: position already within bounds should not change
  it("position already within bounds is unchanged", () => {
    const boxWidth = 0.2;
    const boxHeight = 0.1;
    const x = 0.3;
    const y = 0.4;

    const clamped = clampPosition(x, y, boxWidth, boxHeight);
    expect(clamped.x).toBe(x);
    expect(clamped.y).toBe(y);
  });

  // Edge case: position at exact boundaries (0,0) and (1-boxWidth, 1-boxHeight)
  it("position at exact boundary (0,0) is unchanged", () => {
    const clamped = clampPosition(0, 0, 0.5, 0.5);
    expect(clamped.x).toBe(0);
    expect(clamped.y).toBe(0);
  });

  it("position at exact boundary (1-boxWidth, 1-boxHeight) is unchanged", () => {
    const boxWidth = 0.3;
    const boxHeight = 0.2;
    const clamped = clampPosition(1 - boxWidth, 1 - boxHeight, boxWidth, boxHeight);
    expect(clamped.x).toBeCloseTo(1 - boxWidth);
    expect(clamped.y).toBeCloseTo(1 - boxHeight);
  });

  // Edge case: very large box (boxWidth=1, boxHeight=1) should clamp to (0,0)
  it("very large box (1x1) clamps position to (0,0)", () => {
    const clamped = clampPosition(0.5, 0.5, 1, 1);
    expect(clamped.x).toBe(0);
    expect(clamped.y).toBe(0);
  });
});
