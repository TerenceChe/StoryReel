import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  act,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AxiosError } from "axios";
import * as fc from "fast-check";
import { ToastProvider } from "../components/Toast";
import { ProjectListPage } from "../pages/ProjectListPage";
import { TitleApiError } from "../api/projects";
import type { Project, ProjectSummary } from "../types";

// Mock the API. The page's three seams are `listProjects`,
// `renameProject`, and `deleteProject`; we leave the rest stubbed.
// `TitleApiError` is preserved from the real module so `instanceof`
// checks in the page component continue to work.
vi.mock("../api/projects", async () => {
  const actual = await vi.importActual<typeof import("../api/projects")>(
    "../api/projects",
  );
  return {
    ...actual,
    listProjects: vi.fn(),
    renameProject: vi.fn(),
    deleteProject: vi.fn(),
  };
});

import { listProjects, renameProject } from "../api/projects";

const mockListProjects = listProjects as ReturnType<typeof vi.fn>;
const mockRenameProject = renameProject as ReturnType<typeof vi.fn>;

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <ProjectListPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

function makeSummary(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    id: "proj-1",
    title: "Original Title",
    status: "ready",
    version: 1,
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: "proj-1",
    title: "Renamed",
    storyText: "story",
    voice: "zh-CN-XiaoxiaoNeural",
    status: "ready",
    version: 2,
    pipelineProgress: { stage: "complete", message: "" },
    subtitles: [],
    backgroundImage: null,
    videoUrl: null,
    audioUrl: null,
    audioDuration: null,
    exportUrl: null,
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-02T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  cleanup();
});

// ---------------------------------------------------------------------------
// Example E2: rename input pre-fill and focus.
// Validates Requirement 2.1.
// ---------------------------------------------------------------------------

describe("ProjectListPage rename — Example E2 (Requirement 2.1)", () => {
  it("pre-fills the input with the current title and focuses it", async () => {
    mockListProjects.mockResolvedValue([
      makeSummary({ id: "proj-1", title: "Hello World" }),
    ]);
    renderPage();

    // Wait for the row to render.
    await screen.findByText("Hello World");

    fireEvent.click(screen.getByLabelText("Rename project"));

    const input = (await screen.findByLabelText(
      "Project title",
    )) as HTMLInputElement;
    expect(input.value).toBe("Hello World");
    expect(document.activeElement).toBe(input);
  });
});

// ---------------------------------------------------------------------------
// 8.5 Component tests — rename success, duplicate, and version conflict.
// Validates Requirements 2.2, 2.3, 3.2, 3.3, 6.3, 6.4.
// ---------------------------------------------------------------------------

describe("ProjectListPage rename — success path (Requirements 2.2, 6.3, 6.4)", () => {
  it("on success updates the row with the returned title/version/updatedAt", async () => {
    mockListProjects.mockResolvedValue([
      makeSummary({ id: "proj-1", title: "Old", version: 5 }),
    ]);
    mockRenameProject.mockResolvedValue(
      makeProject({
        id: "proj-1",
        title: "New",
        version: 6,
        updatedAt: "2025-02-01T00:00:00Z",
      }),
    );
    renderPage();

    await screen.findByText("Old");
    fireEvent.click(screen.getByLabelText("Rename project"));
    const input = await screen.findByLabelText("Project title");
    fireEvent.change(input, { target: { value: "New" } });
    fireEvent.click(screen.getByText("Save"));

    // After resolution, the row is back to idle and shows the new title.
    await screen.findByText("New");
    expect(screen.queryByLabelText("Project title")).toBeNull();
    expect(mockRenameProject).toHaveBeenCalledWith("proj-1", "New", 5);

    // Subsequent rename should use the *new* version (6), proving the
    // row's version was advanced (Requirement 6.4).
    mockRenameProject.mockResolvedValue(
      makeProject({
        id: "proj-1",
        title: "Newer",
        version: 7,
        updatedAt: "2025-02-02T00:00:00Z",
      }),
    );
    fireEvent.click(screen.getByLabelText("Rename project"));
    const input2 = await screen.findByLabelText("Project title");
    fireEvent.change(input2, { target: { value: "Newer" } });
    fireEvent.click(screen.getByText("Save"));
    await screen.findByText("Newer");
    expect(mockRenameProject).toHaveBeenLastCalledWith("proj-1", "Newer", 6);
  });
});

describe("ProjectListPage rename — duplicate (Requirements 2.3, 3.2, 3.3)", () => {
  it("renders a TitleApiError(title_duplicate) inline and keeps the input open with the user's draft", async () => {
    mockListProjects.mockResolvedValue([
      makeSummary({ id: "proj-1", title: "First", version: 1 }),
    ]);
    mockRenameProject.mockRejectedValue(
      new TitleApiError(
        "title_duplicate",
        "A project with this title already exists.",
      ),
    );
    renderPage();

    await screen.findByText("First");
    fireEvent.click(screen.getByLabelText("Rename project"));
    const input = (await screen.findByLabelText(
      "Project title",
    )) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Dup" } });
    fireEvent.click(screen.getByText("Save"));

    // Inline error appears.
    await screen.findByText("A project with this title already exists.");
    // The input is still open, still shows the user's draft.
    const stillOpen = (await screen.findByLabelText(
      "Project title",
    )) as HTMLInputElement;
    expect(stillOpen.value).toBe("Dup");
    expect(stillOpen.getAttribute("aria-invalid")).toBe("true");
    // The stored title in the row is unchanged (still "First" — but
    // hidden because we're in editing mode; the header text would
    // re-appear if we cancelled).
    fireEvent.keyDown(stillOpen, { key: "Escape" });
    await screen.findByText("First");
  });
});

describe("ProjectListPage rename — version conflict (Requirement 2.2)", () => {
  it("on a non-title 409 refreshes the list and re-enters editing", async () => {
    // Initial list — version 1.
    mockListProjects.mockResolvedValueOnce([
      makeSummary({ id: "proj-1", title: "Stale", version: 1 }),
    ]);
    // After the conflict, the refreshed list bumps the version.
    mockListProjects.mockResolvedValueOnce([
      makeSummary({ id: "proj-1", title: "Stale", version: 2 }),
    ]);
    // First save: 409 version_conflict (axios error, NOT a structured
    // TitleApiError — the rename endpoint returns a string detail for
    // version conflicts so the API client rethrows the axios error).
    const axiosErr = new AxiosError("Request failed");
    axiosErr.response = {
      status: 409,
      statusText: "Conflict",
      headers: {},
      data: { detail: "Version conflict: expected 2, got 1" },
      config: {} as never,
    };
    mockRenameProject.mockRejectedValueOnce(axiosErr);
    // Second save (after refresh) succeeds.
    mockRenameProject.mockResolvedValueOnce(
      makeProject({
        id: "proj-1",
        title: "Fresh",
        version: 3,
        updatedAt: "2025-02-03T00:00:00Z",
      }),
    );

    renderPage();
    await screen.findByText("Stale");

    fireEvent.click(screen.getByLabelText("Rename project"));
    const input = (await screen.findByLabelText(
      "Project title",
    )) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Fresh" } });
    fireEvent.click(screen.getByText("Save"));

    // After the conflict, the list is re-fetched and the row is back
    // in editing mode with the user's draft preserved.
    await waitFor(() => {
      expect(mockListProjects).toHaveBeenCalledTimes(2);
    });
    const reopened = (await screen.findByLabelText(
      "Project title",
    )) as HTMLInputElement;
    expect(reopened.value).toBe("Fresh");

    // Submitting again uses the refreshed version (2).
    fireEvent.click(screen.getByText("Save"));
    await screen.findByText("Fresh");
    expect(mockRenameProject).toHaveBeenLastCalledWith("proj-1", "Fresh", 2);
  });
});

// ---------------------------------------------------------------------------
// Property 12: Rename in-flight guard.
// Validates: Requirements 2.4
// ---------------------------------------------------------------------------
//
// While `mode === "submitting"`, invoking the submit handler with any
// sequence of arbitrary draft strings produces no additional network
// requests. fast-check generates the draft sequence; the test asserts
// that exactly one `renameProject` call was made regardless of how
// many additional Save clicks (or Enter keypresses on the input)
// occurred while the row was submitting.

describe("ProjectListPage rename — Property 12 (Requirement 2.4)", () => {
  it("in_flight_guard: no additional network requests while submitting", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(fc.string({ minLength: 0, maxLength: 20 }), {
          minLength: 1,
          maxLength: 8,
        }),
        async (drafts) => {
          // Reset between iterations — fast-check reuses the worker.
          cleanup();
          vi.clearAllMocks();

          mockListProjects.mockResolvedValue([
            makeSummary({ id: "proj-1", title: "Original", version: 1 }),
          ]);

          // Deferred promise — never resolves so the row stays in
          // `submitting` for the duration of the test iteration.
          let _resolve: (p: Project) => void = () => {};
          const deferred = new Promise<Project>((res) => {
            _resolve = res;
          });
          mockRenameProject.mockReturnValue(deferred);

          renderPage();
          await screen.findByText("Original");

          fireEvent.click(screen.getByLabelText("Rename project"));
          const input = (await screen.findByLabelText(
            "Project title",
          )) as HTMLInputElement;

          // Initial save with the first draft.
          fireEvent.change(input, { target: { value: drafts[0] } });
          fireEvent.click(screen.getByText("Save"));

          // The button should now read "Saving…" and be disabled.
          const saveButton = screen.getByRole("button", { name: "Saving…" });
          expect((saveButton as HTMLButtonElement).disabled).toBe(true);

          // Now dispatch every remaining draft via a Save click and an
          // Enter keypress on the input. None of these should produce
          // a new network call. The input itself is `disabled` so
          // change events are blocked at the DOM level — that is part
          // of the in-flight guard. We exercise the click and Enter
          // paths to prove the handlers are also no-ops.
          for (let i = 1; i < drafts.length; i++) {
            // Save click — handler should bail because mode === "submitting".
            fireEvent.click(saveButton);
            // Enter keypress on the input — handler should also bail.
            fireEvent.keyDown(input, { key: "Enter" });
          }

          // Exactly one call total.
          expect(mockRenameProject).toHaveBeenCalledTimes(1);
          expect(mockRenameProject).toHaveBeenCalledWith(
            "proj-1",
            drafts[0],
            1,
          );

          // Row state unchanged: still submitting, draft and version
          // unchanged. We verify by checking the input is still
          // disabled and shows the original draft (which is what the
          // page captured at submit time).
          expect(input.disabled).toBe(true);
          expect(input.value).toBe(drafts[0]);

          // Clean up the dangling promise to avoid leaking React
          // updates into the next iteration.
          await act(async () => {
            _resolve(
              makeProject({
                id: "proj-1",
                title: drafts[0] || "x",
                version: 2,
              }),
            );
            // Flush microtasks so the resolved promise is processed
            // before this iteration ends.
          });
        },
      ),
      { numRuns: 25 },
    );
  });
});
