import { describe, it, expect, vi, beforeEach } from "vitest";
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
} from "../api/projects";

// Mock the axios client
vi.mock("../api/client", () => {
  const instance = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
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
  delete: ReturnType<typeof vi.fn>;
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("API client — projects", () => {
  it("createProject sends POST /projects with story text", async () => {
    const fakeProject = { id: "abc", title: "Test" };
    mockClient.post.mockResolvedValue({ data: fakeProject });

    const result = await createProject("Hello world");
    expect(mockClient.post).toHaveBeenCalledWith("/projects", {
      storyText: "Hello world",
    });
    expect(result).toEqual(fakeProject);
  });

  it("createProject includes optional voice and title", async () => {
    mockClient.post.mockResolvedValue({ data: {} });

    await createProject("text", "zh-CN-YunxiNeural", "My Title");
    expect(mockClient.post).toHaveBeenCalledWith("/projects", {
      storyText: "text",
      voice: "zh-CN-YunxiNeural",
      title: "My Title",
    });
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
});
