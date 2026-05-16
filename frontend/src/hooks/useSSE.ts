import { useEffect, useRef, useState } from "react";

export interface SSEState {
  stage: string | null;
  message: string | null;
  isConnected: boolean;
}

const NAMED_EVENTS = [
  "narration",
  "subtitles",
  "assembly",
  "complete",
  "error",
] as const;

const TERMINAL_STAGES = new Set<string>(["complete", "error"]);

/**
 * Custom hook for consuming Server-Sent Event streams.
 *
 * The backend emits events with named types (event: complete, event: narration,
 * etc.), so we register listeners for each known stage in addition to the
 * default `message` event. On a terminal event we close the connection.
 */
export function useSSE(url: string | null): SSEState {
  const [state, setState] = useState<SSEState>({
    stage: null,
    message: null,
    isConnected: false,
  });

  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!url) {
      setState({ stage: null, message: null, isConnected: false });
      return;
    }

    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;

      const es = new EventSource(url!);
      esRef.current = es;

      es.onopen = () => {
        if (!cancelled) {
          setState((prev) => ({ ...prev, isConnected: true }));
        }
      };

      function handle(event: MessageEvent) {
        if (cancelled) return;
        try {
          const data = JSON.parse(event.data);
          // Prefer the named event type if available; fall back to data fields.
          const stage =
            (event.type && event.type !== "message" ? event.type : null) ??
            data.stage ??
            data.event ??
            null;
          const message = data.message ?? data.detail ?? null;

          setState({ stage, message, isConnected: true });

          if (stage && TERMINAL_STAGES.has(stage)) {
            es.close();
            esRef.current = null;
            setState((prev) => ({ ...prev, isConnected: false }));
          }
        } catch {
          // Ignore unparseable messages (e.g. keepalive pings)
        }
      }

      // Default `message` event (used when the server omits `event:`)
      es.onmessage = handle;
      // Named events from the backend
      for (const name of NAMED_EVENTS) {
        es.addEventListener(name, handle as EventListener);
      }

      es.onerror = () => {
        if (cancelled) return;

        // If we already saw a terminal stage, the close was expected.
        let alreadyTerminal = false;
        setState((prev) => {
          alreadyTerminal = prev.stage != null && TERMINAL_STAGES.has(prev.stage);
          return { ...prev, isConnected: false };
        });

        es.close();
        esRef.current = null;

        if (!alreadyTerminal) {
          reconnectTimer = setTimeout(connect, 3000);
        }
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, [url]);

  return state;
}
