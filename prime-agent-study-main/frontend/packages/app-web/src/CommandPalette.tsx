import { t } from "@yal/core";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import styles from "./shell.module.css";

export type CommandItem = {
  id: string;
  label: string;
  to:
    | "/$tenant/catalog"
    | "/$tenant/runs"
    | "/$tenant/observe"
    | "/$tenant/settings"
    | "/$tenant/dev/kitchen-sink";
};

export function CommandPalette({
  open,
  onOpenChange,
  commands,
  tenant,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  commands: CommandItem[];
  tenant: string;
}) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return commands;
    return commands.filter((c) => c.label.toLowerCase().includes(needle));
  }, [commands, q]);

  useEffect(() => {
    if (!open) {
      setQ("");
      setActive(0);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
      }
      if (e.key === "Enter") {
        const item = filtered[active];
        if (item) {
          void navigate({ to: item.to, params: { tenant } });
          onOpenChange(false);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, filtered, active, navigate, onOpenChange, tenant]);

  if (!open) return null;

  return (
    <div
      className={styles.paletteOverlay}
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onOpenChange(false);
      }}
    >
      <div
        className={styles.palette}
        role="dialog"
        aria-modal="true"
        aria-label={t("shell.commandPalette.title")}
      >
        <input
          autoFocus
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setActive(0);
          }}
          placeholder={t("shell.search.placeholder")}
        />
        <ul>
          {filtered.length === 0 ? (
            <li>
              <button type="button" disabled>
                {t("shell.commandPalette.empty")}
              </button>
            </li>
          ) : (
            filtered.map((c, i) => (
              <li key={c.id}>
                <button
                  type="button"
                  data-active={i === active}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => {
                    void navigate({ to: c.to, params: { tenant } });
                    onOpenChange(false);
                  }}
                >
                  {c.label}
                </button>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
