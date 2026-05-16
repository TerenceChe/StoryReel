import { useEffect, useState } from "react";

/**
 * Shows a banner when the browser is offline.
 * Listens to the native online/offline events.
 */
export function ConnectionStatus() {
  const [online, setOnline] = useState(
    typeof navigator !== "undefined" ? navigator.onLine : true,
  );

  useEffect(() => {
    const goOnline = () => setOnline(true);
    const goOffline = () => setOnline(false);

    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);

    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  if (online) return null;

  return (
    <div
      role="status"
      aria-live="assertive"
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        background: "#e65100",
        color: "#fff",
        textAlign: "center",
        padding: "8px 16px",
        fontSize: 14,
        zIndex: 10000,
      }}
    >
      You are offline. Some features may be unavailable.
    </div>
  );
}
