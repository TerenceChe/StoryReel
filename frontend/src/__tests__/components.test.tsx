import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { ToastProvider, useToast } from "../components/Toast";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { ConnectionStatus } from "../components/ConnectionStatus";

// --- Toast tests ---

function ToastTrigger() {
  const { showToast } = useToast();
  return (
    <button onClick={() => showToast("Something went wrong")}>
      Show Toast
    </button>
  );
}

describe("Toast", () => {
  it("shows a toast message when triggered", () => {
    render(
      <ToastProvider>
        <ToastTrigger />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByText("Show Toast"));
    expect(screen.getByText("Something went wrong")).toBeTruthy();
  });

  it("dismisses toast when close button is clicked", () => {
    render(
      <ToastProvider>
        <ToastTrigger />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByText("Show Toast"));
    expect(screen.getByText("Something went wrong")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Dismiss notification"));
    expect(screen.queryByText("Something went wrong")).toBeNull();
  });

  it("throws when useToast is used outside provider", () => {
    // Suppress console.error for this test
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<ToastTrigger />)).toThrow(
      "useToast must be used within a ToastProvider",
    );
    spy.mockRestore();
  });
});

// --- ErrorBoundary tests ---

function ThrowingComponent({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) throw new Error("Test error");
  return <div>All good</div>;
}

describe("ErrorBoundary", () => {
  it("renders children when no error", () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent shouldThrow={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("All good")).toBeTruthy();
  });

  it("renders fallback UI on error", () => {
    // Suppress console.error for the expected error
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <ThrowingComponent shouldThrow={true} />
      </ErrorBoundary>,
    );

    expect(screen.getByText("Something went wrong")).toBeTruthy();
    expect(screen.getByText("Test error")).toBeTruthy();
    expect(screen.getByText("Reload page")).toBeTruthy();

    spy.mockRestore();
  });
});

// --- ConnectionStatus tests ---

describe("ConnectionStatus", () => {
  it("renders nothing when online", () => {
    Object.defineProperty(navigator, "onLine", {
      value: true,
      writable: true,
      configurable: true,
    });

    const { container } = render(<ConnectionStatus />);
    expect(container.innerHTML).toBe("");
  });

  it("shows offline banner when offline", () => {
    Object.defineProperty(navigator, "onLine", {
      value: false,
      writable: true,
      configurable: true,
    });

    render(<ConnectionStatus />);
    expect(screen.getByText(/You are offline/)).toBeTruthy();
  });

  it("reacts to online/offline events", () => {
    Object.defineProperty(navigator, "onLine", {
      value: true,
      writable: true,
      configurable: true,
    });

    render(<ConnectionStatus />);
    expect(screen.queryByText(/You are offline/)).toBeNull();

    // Go offline
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByText(/You are offline/)).toBeTruthy();

    // Go back online
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(screen.queryByText(/You are offline/)).toBeNull();
  });
});
