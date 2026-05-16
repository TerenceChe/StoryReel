import { createContext, useContext, useState, useCallback, useMemo } from "react";
import type { ReactNode } from "react";

interface UnsavedChangesContextValue {
  /** True when at least one editor has reported unsaved edits. */
  isDirty: boolean;
  /** Register a dirty source. The returned cleanup unregisters it. */
  setDirty: (key: string, dirty: boolean) => void;
  /** Show the standard confirm dialog. Returns true if the user wants to proceed. */
  confirmDiscard: () => boolean;
}

const Ctx = createContext<UnsavedChangesContextValue>({
  isDirty: false,
  setDirty: () => {},
  confirmDiscard: () => true,
});

export function UnsavedChangesProvider({ children }: { children: ReactNode }) {
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(() => new Set());

  const setDirty = useCallback((key: string, dirty: boolean) => {
    setDirtyKeys((prev) => {
      const has = prev.has(key);
      if (dirty && !has) {
        const next = new Set(prev);
        next.add(key);
        return next;
      }
      if (!dirty && has) {
        const next = new Set(prev);
        next.delete(key);
        return next;
      }
      return prev;
    });
  }, []);

  const isDirty = dirtyKeys.size > 0;

  const confirmDiscard = useCallback(() => {
    if (!isDirty) return true;
    return window.confirm(
      "You have unsaved changes. Leave this page and discard them?",
    );
  }, [isDirty]);

  const value = useMemo(
    () => ({ isDirty, setDirty, confirmDiscard }),
    [isDirty, setDirty, confirmDiscard],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useUnsavedChanges() {
  return useContext(Ctx);
}
