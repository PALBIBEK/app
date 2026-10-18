import * as RToast from "@radix-ui/react-toast";
import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";
import styles from "./Toast.module.css";

type ToastItem = { id: string; title: string; actionLabel?: string; onAction?: () => void };

const Ctx = createContext<{ push: (t: Omit<ToastItem, "id">) => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((t: Omit<ToastItem, "id">) => {
    setItems((prev) => [...prev, { ...t, id: crypto.randomUUID() }]);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <Ctx.Provider value={value}>
      <RToast.Provider swipeDirection="right">
        {children}
        {items.map((item) => (
          <RToast.Root
            key={item.id}
            className={styles.root}
            open
            onOpenChange={(open) => {
              if (!open) setItems((prev) => prev.filter((x) => x.id !== item.id));
            }}
          >
            <RToast.Title>{item.title}</RToast.Title>
            {item.actionLabel ? (
              <button type="button" className={styles.action} onClick={item.onAction}>
                {item.actionLabel}
              </button>
            ) : null}
          </RToast.Root>
        ))}
        <RToast.Viewport className={styles.viewport} />
      </RToast.Provider>
    </Ctx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast requires ToastProvider");
  return ctx;
}
