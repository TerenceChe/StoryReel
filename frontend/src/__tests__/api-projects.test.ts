import { describe, it, expect, vi, beforeEach } from "vitest";
import { AxiosError } from "axios";
import apiClient from "../api/client";
import {
  createProject,
  listProjects,
  getProject,
  updateProject,
  deleteProject,
  triggerExport,
  retryPipeline,
  listVoices,
  renameProject,
  TitleApiError,
} from "../api/projects";

// Mock the axios client
vi.mock("../api/client", () => {
  const instance = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  };
  return { default: instance, toSnakeCase: (d: unknown) => d, toCamelCase: (d: unknown) => d };
});

const mockClient = apiClient as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  put: ReturnType<typeof vi.fn>;
  patch: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("API client — projects", () => {
  it("createProject sends POST /projects with story text and title", async () => {
    const fakeProject = { id: "abc", title: "Test" };
    mockClient.post.mockResolvedValue({ data: fakeProject });

    const result = await createProject("Hello world", "My Title");
    expect(mockClient.post).toHaveBeenCalledWith("/projects", {
      storyText: "Hello world",
      title: "My Title",
    });
    expect(result).toEqual(fakeProject);
  });

  it("createProject includes optional voice when provided", async () => {
    mockClient.post.mockResolvedValue({ data: {} });

    await createProject("text", "My Title", "zh-CN-YunxiNeural");
    expect(mockClient.post).toHaveBeenCalledWith("/projects", {
      storyText: "text",
      title: "My Title",
      voice: "zh-CN-YunxiNeural",
    });
  });

  it("createProject wraps a structured title error in TitleApiError", async () => {
    const axiosErr = new AxiosError("Request failed");
    axiosErr.response = {
      status: 409,
      statusText: "Conflict",
      headers: {},
      // Response interceptor has converted snake_case → camelCase before we see it.
      data: {
        detail: {
          errorCode: "title_duplicate",
          field: "title",
          message: "A project with this title already exists.",
        },
      },
      config: {} as never,
    };
    mockClient.post.mockRejectedValue(axiosErr);

    await expect(createProject("text", "Dup")).rejects.toMatchObject({
      name: "TitleApiError",
      code: "title_duplicate",
      field: "title",
      message: "A project with this title already exists.",
    });
  });

  it("createProject rethrows non-title errors unchanged", async () => {
    const axiosErr = new AxiosError("server boom");
    axiosErr.response = {
      status: 500,
      statusText: "Internal Server Error",
      headers: {},
      data: { detail: "boom" },
      config: {} as never,
    };
    mockClient.post.mockRejectedValue(axiosErr);

    await expect(createProject("text", "Title")).rejects.toBe(axiosErr);
  });

  it("listProjects sends GET /projects", async () => {
    mockClient.get.mockResolvedValue({ data: [{ id: "1" }] });

    const result = await listProjects();
    expect(mockClient.get).toHaveBeenCalledWith("/projects");
    expect(result).toEqual([{ id: "1" }]);
  });

  it("getProject sends GET /projects/:id", async () => {
    mockClient.get.mockResolvedValue({ data: { id: "xyz" } });

    const result = await getProject("xyz");
    expect(mockClient.get).toHaveBeenCalledWith("/projects/xyz");
    expect(result).toEqual({ id: "xyz" });
  });

  it("updateProject sends PUT /projects/:id", async () => {
    mockClient.put.mockResolvedValue({ data: { id: "xyz", version: 2 } });

    const result = await updateProject("xyz", { version: 1 });
    expect(mockClient.put).toHaveBeenCalledWith("/projects/xyz", {
      version: 1,
    });
    expect(result).toEqual({ id: "xyz", version: 2 });
  });

  it("deleteProject sends DELETE /projects/:id", async () => {
    mockClient.delete.mockResolvedValue({});

    await deleteProject("abc");
    expect(mockClient.delete).toHaveBeenCalledWith("/projects/abc");
  });

  it("triggerExport sends POST /projects/:id/export", async () => {
    mockClient.post.mockResolvedValue({
      data: { detail: "started", projectId: "abc" },
    });

    const result = await triggerExport("abc");
    expect(mockClient.post).toHaveBeenCalledWith("/projects/abc/export", undefined);
    expect(result.detail).toBe("started");
  });

  it("retryPipeline sends POST /projects/:id/retry", async () => {
    mockClient.post.mockResolvedValue({
      data: { detail: "retrying", projectId: "abc" },
    });

    const result = await retryPipeline("abc");
    expect(mockClient.post).toHaveBeenCalledWith("/projects/abc/retry");
    expect(result.detail).toBe("retrying");
  });

  it("listVoices sends GET /voices", async () => {
    const voices = [{ id: "zh-CN-XiaoxiaoNeural", name: "Xiaoxiao" }];
    mockClient.get.mockResolvedValue({ data: voices });

    const result = await listVoices();
    expect(mockClient.get).toHaveBeenCalledWith("/voices");
    expect(result).toEqual(voices);
  });

  it("renameProject sends PATCH /projects/:id/title with title and version", async () => {
    const updated = { id: "abc", title: "Renamed", version: 3 };
    mockClient.patch.mockResolvedValue({ data: updated });

    const result = await renameProject("abc", "Renamed", 2);
    expect(mockClient.patch).toHaveBeenCalledWith("/projects/abc/title", {
      title: "Renamed",
      version: 2,
    });
    expect(result).toEqual(updated);
  });

  it("renameProject wraps a structured title error in TitleApiError", async () => {
    const axiosErr = new AxiosError("Request failed");
    axiosErr.response = {
      status: 422,
      statusText: "Unprocessable Entity",
      headers: {},
      data: {
        detail: {
          errorCode: "title_too_long",
          field: "title",
          message: "Title must be at most 100 characters.",
        },
      },
      config: {} as never,
    };
    mockClient.patch.mockRejectedValue(axiosErr);

    const promise = renameProject("abc", "x".repeat(200), 1);
    await expect(promise).rejects.toBeInstanceOf(TitleApiError);
    await expect(promise).rejects.toMatchObject({
      code: "title_too_long",
      field: "title",
    });
  });
});
