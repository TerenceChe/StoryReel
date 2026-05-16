import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSSE } from "../hooks/useSSE";

// Mock EventSource
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 0;
  closed = false;
  listeners: Record<string, Array<(event: { type: string; data: string }) => void>> = {};

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: (event: { type: string; data: string }) => void) {
    (this.listeners[type] ||= []).push(fn);
  }

  removeEventListener(type: string, fn: (event: { type: string; data: string }) => void) {
    this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
  }

  /** Helper for tests: dispatch a named event. */
  dispatch(type: string, data: string) {
    for (const fn of this.listeners[type] || []) {
      fn({ type, data });
    }
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useSSE", () => {
  it("returns disconnected state when url is null", () => {
    const { result } = renderHook(() => useSSE(null));
    expect(result.current.stage).toBeNull();
    expect(result.current.message).toBeNull();
    expect(result.current.isConnected).toBe(false);
  });

  it("connects to the given URL", () => {
    renderHook(() => useSSE("http://localhost/sse"));
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toBe("http://localhost/sse");
  });

  it("sets isConnected on open", () => {
    const { result } = renderHook(() => useSSE("http://localhost/sse"));
    const es = MockEventSource.instances[0];

    act(() => {
      es.onopen?.();
    });

    expect(result.current.isConnected).toBe(true);
  });

  it("parses SSE message data from default message event", () => {
    const { result } = renderHook(() => useSSE("http://localhost/sse"));
    const es = MockEventSource.instances[0];

    act(() => {
      es.onopen?.();
      es.onmessage?.({
        data: JSON.stringify({ stage: "narration", message: "Generating..." }),
      });
    });

    expect(result.current.stage).toBe("narration");
    expect(result.current.message).toBe("Generating...");
  });

  it("parses named SSE events (event: complete)", () => {
    const { result } = renderHook(() => useSSE("http://localhost/sse"));
    const es = MockEventSource.instances[0];

    act(() => {
      es.onopen?.();
      es.dispatch(
        "complete",
        JSON.stringify({ stage: "complete", message: "Done" }),
      );
    });

    expect(result.current.stage).toBe("complete");
    expect(result.current.message).toBe("Done");
    expect(es.closed).toBe(true);
  });

  it("closes connection on complete stage", () => {
    const { result } = renderHook(() => useSSE("http://localhost/sse"));
    const es = MockEventSource.instances[0];

    act(() => {
      es.onopen?.();
      es.onmessage?.({
        data: JSON.stringify({ stage: "complete", message: "Done" }),
      });
    });

    expect(es.closed).toBe(true);
    expect(result.current.stage).toBe("complete");
    expect(result.current.isConnected).toBe(false);
  });

  it("closes connection on error stage", () => {
    const { result } = renderHook(() => useSSE("http://localhost/sse"));
    const es = MockEventSource.instances[0];

    act(() => {
      es.onopen?.();
      es.onmessage?.({
        data: JSON.stringify({ stage: "error", message: "Failed" }),
      });
    });

    expect(es.closed).toBe(true);
    expect(result.current.stage).toBe("error");
  });

  it("closes EventSource on unmount", () => {
    const { unmount } = renderHook(() => useSSE("http://localhost/sse"));
    const es = MockEventSource.instances[0];

    unmount();
    expect(es.closed).toBe(true);
  });
});
