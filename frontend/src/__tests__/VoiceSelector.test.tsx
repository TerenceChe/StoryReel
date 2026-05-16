import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { VoiceSelector } from "../components/VoiceSelector";

vi.mock("../api/projects", () => ({
  listVoices: vi.fn(),
}));

import { listVoices } from "../api/projects";
const mockListVoices = listVoices as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("VoiceSelector", () => {
  it("shows loading state initially", () => {
    mockListVoices.mockReturnValue(new Promise(() => {}));
    render(<VoiceSelector value="zh-CN-XiaoxiaoNeural" onChange={() => {}} />);
    expect(screen.getByText("Loading voices…")).toBeTruthy();
  });

  it("renders voices after loading", async () => {
    mockListVoices.mockResolvedValue([
      { id: "zh-CN-XiaoxiaoNeural", name: "Xiaoxiao", language: "zh-CN" },
      { id: "zh-CN-YunxiNeural", name: "Yunxi", language: "zh-CN" },
    ]);

    render(<VoiceSelector value="zh-CN-XiaoxiaoNeural" onChange={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("Xiaoxiao (zh-CN)")).toBeTruthy();
      expect(screen.getByText("Yunxi (zh-CN)")).toBeTruthy();
    });
  });

  it("calls onChange when a voice is selected", async () => {
    mockListVoices.mockResolvedValue([
      { id: "zh-CN-XiaoxiaoNeural", name: "Xiaoxiao", language: "zh-CN" },
      { id: "zh-CN-YunxiNeural", name: "Yunxi", language: "zh-CN" },
    ]);
    const onChange = vi.fn();

    render(<VoiceSelector value="zh-CN-XiaoxiaoNeural" onChange={onChange} />);

    await waitFor(() => {
      expect(screen.getByText("Xiaoxiao (zh-CN)")).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("Voice"), {
      target: { value: "zh-CN-YunxiNeural" },
    });
    expect(onChange).toHaveBeenCalledWith("zh-CN-YunxiNeural");
  });

  it("shows error state when fetch fails", async () => {
    mockListVoices.mockRejectedValue(new Error("Network error"));

    render(<VoiceSelector value="zh-CN-XiaoxiaoNeural" onChange={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load voices")).toBeTruthy();
    });
  });
});
