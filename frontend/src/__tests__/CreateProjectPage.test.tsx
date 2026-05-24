import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../components/Toast";
import { CreateProjectPage } from "../pages/CreateProjectPage";
import { TitleApiError } from "../api/projects";

// Mock the API. `createProject` is the system-under-test seam; the
// other exports are pulled in transitively so we provide stubs.
vi.mock("../api/projects", async () => {
  // Preserve the real `TitleApiError` class so `instanceof` checks in
  // the page component still work against errors thrown from the mock.
  const actual = await vi.importActual<typeof import("../api/projects")>(
    "../api/projects",
  );
  return {
    ...actual,
    createProject: vi.fn(),
    listVoices: vi.fn().mockResolvedValue([
      { id: "zh-CN-XiaoxiaoNeural", name: "Xiaoxiao", language: "zh-CN" },
      { id: "zh-CN-YunxiNeural", name: "Yunxi", language: "zh-CN" },
    ]),
  };
});

// Mock navigate
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

import { createProject } from "../api/projects";
const mockCreateProject = createProject as ReturnType<typeof vi.fn>;

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <CreateProjectPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CreateProjectPage", () => {
  it("renders the form elements", async () => {
    renderPage();
    expect(screen.getByLabelText("Title")).toBeTruthy();
    expect(screen.getByLabelText("Story Text")).toBeTruthy();
    expect(screen.getByText("Upload .txt file")).toBeTruthy();
    expect(screen.getByText("Create Project")).toBeTruthy();
    // Wait for voices to load
    await waitFor(() => {
      expect(screen.getByLabelText("Voice")).toBeTruthy();
    });
  });

  it("disables submit while the title is empty", () => {
    renderPage();
    const button = screen.getByText("Create Project") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("does not show a title error on initial render", () => {
    renderPage();
    expect(screen.queryByText("Title is required.")).toBeNull();
  });

  it("shows inline title error after submit attempt with empty title and makes no network call", () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("Story Text"), {
      target: { value: "从前有一个小女孩" },
    });
    // Click via the form-submit path even though the button is disabled
    // by attempting to submit the form directly. We blur the title to
    // force the touched state so the live error appears.
    fireEvent.blur(screen.getByLabelText("Title"));
    expect(screen.getByText("Title is required.")).toBeTruthy();
    // Even if a user managed to click submit, no network call goes out.
    fireEvent.click(screen.getByText("Create Project"));
    expect(mockCreateProject).not.toHaveBeenCalled();
  });

  it("shows validation error for whitespace-only story text once title is valid", () => {
    mockCreateProject.mockResolvedValue({ id: "proj-123" });
    renderPage();
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "My Story" },
    });
    fireEvent.change(screen.getByLabelText("Story Text"), {
      target: { value: "   \n\t  " },
    });
    fireEvent.click(screen.getByText("Create Project"));
    expect(
      screen.getByText("Story text cannot be empty or whitespace-only."),
    ).toBeTruthy();
    expect(mockCreateProject).not.toHaveBeenCalled();
  });

  it("submits valid title + text and navigates to editor", async () => {
    mockCreateProject.mockResolvedValue({ id: "proj-123" });
    renderPage();

    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "My Story" },
    });
    fireEvent.change(screen.getByLabelText("Story Text"), {
      target: { value: "从前有一个小女孩" },
    });
    fireEvent.click(screen.getByText("Create Project"));

    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenCalledWith(
        "从前有一个小女孩",
        "My Story",
        "zh-CN-XiaoxiaoNeural",
      );
      expect(mockNavigate).toHaveBeenCalledWith("/projects/proj-123");
    });
  });

  it("trims the title before sending", async () => {
    mockCreateProject.mockResolvedValue({ id: "proj-123" });
    renderPage();

    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "  Padded  " },
    });
    fireEvent.change(screen.getByLabelText("Story Text"), {
      target: { value: "story" },
    });
    fireEvent.click(screen.getByText("Create Project"));

    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenCalledWith(
        "story",
        "Padded",
        "zh-CN-XiaoxiaoNeural",
      );
    });
  });

  it("shows a live shape error for an over-long title once touched", () => {
    renderPage();
    const titleInput = screen.getByLabelText("Title");
    // 101 ASCII chars — over the 100 code-point cap.
    fireEvent.change(titleInput, { target: { value: "a".repeat(101) } });
    fireEvent.blur(titleInput);
    expect(
      screen.getByText("Title must be at most 100 characters."),
    ).toBeTruthy();
  });

  it("renders a TitleApiError(title_duplicate) inline next to the title input", async () => {
    mockCreateProject.mockRejectedValue(
      new TitleApiError(
        "title_duplicate",
        "A project with this title already exists.",
      ),
    );
    renderPage();

    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Dup" },
    });
    fireEvent.change(screen.getByLabelText("Story Text"), {
      target: { value: "story" },
    });
    fireEvent.click(screen.getByText("Create Project"));

    await waitFor(() => {
      expect(
        screen.getByText("A project with this title already exists."),
      ).toBeTruthy();
    });
    // The inline error should be wired up via aria-describedby.
    const titleInput = screen.getByLabelText("Title") as HTMLInputElement;
    expect(titleInput.getAttribute("aria-invalid")).toBe("true");
    expect(titleInput.getAttribute("aria-describedby")).toBe(
      "project-title-error",
    );
  });

  it("clears story-text validation error when user types in the textarea", () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "My Story" },
    });
    fireEvent.click(screen.getByText("Create Project"));
    expect(
      screen.getByText("Story text cannot be empty or whitespace-only."),
    ).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Story Text"), {
      target: { value: "a" },
    });
    expect(
      screen.queryByText("Story text cannot be empty or whitespace-only."),
    ).toBeNull();
  });

  it("shows loading state while submitting", async () => {
    // Never resolve to keep the loading state
    mockCreateProject.mockReturnValue(new Promise(() => {}));
    renderPage();

    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "My Story" },
    });
    fireEvent.change(screen.getByLabelText("Story Text"), {
      target: { value: "test" },
    });
    fireEvent.click(screen.getByText("Create Project"));

    await waitFor(() => {
      expect(screen.getByText("Creating…")).toBeTruthy();
    });
  });
});
