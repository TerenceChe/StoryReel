import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Timeline } from "../components/Timeline";
import type { SubtitleSegment } from "../types";

/** Helper to create a subtitle segment with minimal required fields. */
function makeSub(
  id: string,
  startTime: number,
  endTime: number,
  text = `Sub ${id}`,
): SubtitleSegment {
  return {
    id,
    text,
    startTime,
    endTime,
    position: { x: 0.5, y: 0.85 },
    style: {
      fontSize: 0.047,
      fontColor: "#FFFFFF",
      outlineColor: "#000000",
      fontFamily: "Noto Sans CJK SC",
    },
  };
}

const noop = () => {};

describe("Timeline", () => {
  it("renders unavailable message when audioDuration is null", () => {
    render(
      <Timeline
        subtitles={[]}
        audioDuration={null}
        currentTime={0}
        selectedSubtitleId={null}
        onSubtitleSelect={noop}
        onTimingChange={noop}
        onSeek={noop}
      />,
    );
    expect(screen.getByText(/Timeline unavailable/)).toBeTruthy();
  });

  it("renders unavailable message when audioDuration is 0", () => {
    render(
      <Timeline
        subtitles={[]}
        audioDuration={0}
        currentTime={0}
        selectedSubtitleId={null}
        onSubtitleSelect={noop}
        onTimingChange={noop}
        onSeek={noop}
      />,
    );
    expect(screen.getByText(/Timeline unavailable/)).toBeTruthy();
  });

  it("renders subtitle blocks with text labels", () => {
    const subs = [
      makeSub("a", 0, 2, "Hello"),
      makeSub("b", 3, 5, "World"),
    ];
    render(
      <Timeline
        subtitles={subs}
        audioDuration={10}
        currentTime={0}
        selectedSubtitleId={null}
        onSubtitleSelect={noop}
        onTimingChange={noop}
        onSeek={noop}
      />,
    );
    expect(screen.getByText("Hello")).toBeTruthy();
    expect(screen.getByText("World")).toBeTruthy();
  });

  it("renders time labels showing 0:00 and total duration", () => {
    render(
      <Timeline
        subtitles={[]}
        audioDuration={65}
        currentTime={0}
        selectedSubtitleId={null}
        onSubtitleSelect={noop}
        onTimingChange={noop}
        onSeek={noop}
      />,
    );
    expect(screen.getByText("0:00")).toBeTruthy();
    expect(screen.getByText("1:05")).toBeTruthy();
  });

  it("calls onSubtitleSelect when a block is clicked", () => {
    const onSelect = vi.fn();
    const subs = [makeSub("a", 0, 2, "Click me")];
    render(
      <Timeline
        subtitles={subs}
        audioDuration={10}
        currentTime={0}
        selectedSubtitleId={null}
        onSubtitleSelect={onSelect}
        onTimingChange={noop}
        onSeek={noop}
      />,
    );
    fireEvent.click(screen.getByText("Click me"));
    expect(onSelect).toHaveBeenCalledWith("a");
  });

  it("calls onSeek when clicking the timeline background", () => {
    const onSeek = vi.fn();
    render(
      <Timeline
        subtitles={[]}
        audioDuration={10}
        currentTime={0}
        selectedSubtitleId={null}
        onSubtitleSelect={noop}
        onTimingChange={noop}
        onSeek={onSeek}
      />,
    );
    const slider = screen.getByRole("slider");
    // Simulate a click at a position — getBoundingClientRect is mocked by jsdom
    fireEvent.click(slider, { clientX: 50 });
    expect(onSeek).toHaveBeenCalled();
  });

  it("renders the playhead marker", () => {
    const { container } = render(
      <Timeline
        subtitles={[]}
        audioDuration={10}
        currentTime={5}
        selectedSubtitleId={null}
        onSubtitleSelect={noop}
        onTimingChange={noop}
        onSeek={noop}
      />,
    );
    // The playhead is a div with red background at 50%
    const playhead = container.querySelector(
      'div[style*="background: rgb(255, 82, 82)"]',
    );
    expect(playhead).toBeTruthy();
  });

  it("highlights the selected subtitle block", () => {
    const subs = [makeSub("a", 0, 2, "Selected")];
    const { container } = render(
      <Timeline
        subtitles={subs}
        audioDuration={10}
        currentTime={0}
        selectedSubtitleId="a"
        onSubtitleSelect={noop}
        onTimingChange={noop}
        onSeek={noop}
      />,
    );
    // Selected block should have a white border
    const block = container.querySelector('div[title*="Selected"]');
    expect(block).toBeTruthy();
    expect((block as HTMLElement).style.border).toContain("2px solid");
  });

  it("stacks overlapping segments into different rows", () => {
    // Two overlapping segments should be in different rows (different top values)
    const subs = [
      makeSub("a", 0, 5, "First"),
      makeSub("b", 2, 7, "Second"),
    ];
    const { container } = render(
      <Timeline
        subtitles={subs}
        audioDuration={10}
        currentTime={0}
        selectedSubtitleId={null}
        onSubtitleSelect={noop}
        onTimingChange={noop}
        onSeek={noop}
      />,
    );
    const firstBlock = container.querySelector('div[title*="First"]') as HTMLElement;
    const secondBlock = container.querySelector('div[title*="Second"]') as HTMLElement;
    expect(firstBlock).toBeTruthy();
    expect(secondBlock).toBeTruthy();
    // They should have different top positions
    expect(firstBlock.style.top).not.toBe(secondBlock.style.top);
  });

  it("places non-overlapping segments in the same row", () => {
    const subs = [
      makeSub("a", 0, 2, "First"),
      makeSub("b", 3, 5, "Second"),
    ];
    const { container } = render(
      <Timeline
        subtitles={subs}
        audioDuration={10}
        currentTime={0}
        selectedSubtitleId={null}
        onSubtitleSelect={noop}
        onTimingChange={noop}
        onSeek={noop}
      />,
    );
    const firstBlock = container.querySelector('div[title*="First"]') as HTMLElement;
    const secondBlock = container.querySelector('div[title*="Second"]') as HTMLElement;
    expect(firstBlock.style.top).toBe(secondBlock.style.top);
  });
});
