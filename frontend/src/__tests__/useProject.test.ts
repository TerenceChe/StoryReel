import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useProject } from "../hooks/useProject";
import * as api from "../api/projects";
import type { Project } from "../types";

vi.mock("../api/projects");

const mockProject: Project = {
  id: "test-id",
  title: "Test",
  storyText: "Hello",
  voice: "zh-CN-XiaoxiaoNeural",
  status: "ready",
  version: 1,
  pipelineProgress: { stage: "complete", message: "Done" },
  subtitles: [],
  backgroundImage: null,
  videoUrl: null,
  audioUrl: null,
  audioDuration: null,
  exportUrl: null,
  createdAt: "2024-01-01T00:00:00Z",
  updatedAt: "2024-01-01T00:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  // Reset draft cache between tests so we always start from server state
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe("useProject", () => {
  it("returns null project when id is null", () => {
    const { result } = renderHook(() => useProject(null));
    expect(result.current.project).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("fetches project on mount", async () => {
    vi.mocked(api.getProject).mockResolvedValue(mockProject);

    const { result } = renderHook(() => useProject("test-id"));

    await waitFor(() => {
      expect(result.current.project).toEqual(mockProject);
    });
    expect(api.getProject).toHaveBeenCalledWith("test-id");
  });

  it("sets error on fetch failure", async () => {
    vi.mocked(api.getProject).mockRejectedValue(new Error("Not found"));

    const { result } = renderHook(() => useProject("test-id"));

    await waitFor(() => {
      expect(result.current.error).toBe("Not found");
    });
  });

  it("applyLocalEdit mutates the draft and marks dirty", async () => {
    vi.mocked(api.getProject).mockResolvedValue(mockProject);
    const { result } = renderHook(() => useProject("test-id"));

    await waitFor(() => {
      expect(result.current.project).toBeTruthy();
    });

    expect(result.current.isDirty).toBe(false);

    act(() => {
      result.current.applyLocalEdit({ title: "New" });
    });

    expect(result.current.project?.title).toBe("New");
    expect(result.current.isDirty).toBe(true);
    // No PUT was issued
    expect(api.updateProject).not.toHaveBeenCalled();
  });

  it("save persists the draft and clears dirty", async () => {
    vi.mocked(api.getProject).mockResolvedValue(mockProject);
    vi.mocked(api.updateProject).mockResolvedValue({
      ...mockProject,
      title: "New",
      version: 2,
    });

    const { result } = renderHook(() => useProject("test-id"));

    await waitFor(() => {
      expect(result.current.project).toBeTruthy();
    });

    act(() => {
      result.current.applyLocalEdit({ title: "New" });
    });

    await act(async () => {
      const ok = await result.current.save();
      expect(ok).toBe(true);
    });

    expect(api.updateProject).toHaveBeenCalledWith(
      "test-id",
      expect.objectContaining({ title: "New", version: 1 }),
    );
    expect(result.current.isDirty).toBe(false);
    expect(result.current.saveStatus).toBe("saved");
  });

  it("save reports error on failure", async () => {
    vi.mocked(api.getProject).mockResolvedValue(mockProject);
    vi.mocked(api.updateProject).mockRejectedValue(new Error("nope"));

    const { result } = renderHook(() => useProject("test-id"));
    await waitFor(() => expect(result.current.project).toBeTruthy());

    act(() => {
      result.current.applyLocalEdit({ title: "New" });
    });

    await act(async () => {
      const ok = await result.current.save();
      expect(ok).toBe(false);
    });

    expect(result.current.saveStatus).toBe("error");
  });

  it("refreshProject fetches latest state", async () => {
    vi.mocked(api.getProject).mockResolvedValue(mockProject);

    const { result } = renderHook(() => useProject("test-id"));

    await waitFor(() => {
      expect(result.current.project).toBeTruthy();
    });

    const refreshed = { ...mockProject, version: 5 };
    vi.mocked(api.getProject).mockResolvedValue(refreshed);

    await act(async () => {
      await result.current.refreshProject();
    });

    expect(result.current.project?.version).toBe(5);
  });
});
