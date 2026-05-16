import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../components/Toast";
import { CreateProjectPage } from "../pages/CreateProjectPage";

// Mock the API
vi.mock("../api/projects", () => ({
  createProject: vi.fn(),
  listVoices: vi.fn().mockResolvedValue([
    { id: "zh-CN-XiaoxiaoNeural", name: "Xiaoxiao", language: "zh-CN" },
    { id: "zh-CN-YunxiNeural", name: "Yunxi", language: "zh-CN" },
  ]),
}));

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
    expect(screen.getByLabelText("Story Text")).toBeTruthy();
    expect(screen.getByText("Upload .txt file")).toBeTruthy();
    expect(screen.getByText("Create Project")).toBeTruthy();
    // Wait for voices to load
    await waitFor(() => {
      expect(screen.getByLabelText("Voice")).toBeTruthy();
    });
  });

  it("shows validation error for empty text", async () => {
    renderPage();
    fireEvent.click(screen.getByText("Create Project"));
    expect(
      screen.getByText("Story text cannot be empty or whitespace-only."),
    ).toBeTruthy();
    expect(mockCreateProject).not.toHaveBeenCalled();
  });

  it("shows validation error for whitespace-only text", async () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("Story Text"), {
      target: { value: "   \n\t  " },
    });
    fireEvent.click(screen.getByText("Create Project"));
    expect(
      screen.getByText("Story text cannot be empty or whitespace-only."),
    ).toBeTruthy();
    expect(mockCreateProject).not.toHaveBeenCalled();
  });

  it("submits valid text and navigates to editor", async () => {
    mockCreateProject.mockResolvedValue({ id: "proj-123" });
    renderPage();

    fireEvent.change(screen.getByLabelText("Story Text"), {
      target: { value: "从前有一个小女孩" },
    });
    fireEvent.click(screen.getByText("Create Project"));

    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenCalledWith(
        "从前有一个小女孩",
        "zh-CN-XiaoxiaoNeural",
      );
      expect(mockNavigate).toHaveBeenCalledWith("/projects/proj-123");
    });
  });

  it("clears validation error when user types", async () => {
    renderPage();
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

    fireEvent.change(screen.getByLabelText("Story Text"), {
      target: { value: "test" },
    });
    fireEvent.click(screen.getByText("Create Project"));

    await waitFor(() => {
      expect(screen.getByText("Creating…")).toBeTruthy();
    });
  });
});
