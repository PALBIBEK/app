import { t } from "@yal/core";
import {
  Avatar,
  Button,
  IconButton,
  Menu,
  ToastProvider,
} from "@yal/ui";
import {
  Link,
  Outlet,
  useParams,
  useRouterState,
} from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { CommandPalette, type CommandItem } from "./CommandPalette";
import styles from "./shell.module.css";

type ThemeMode = "light" | "dark" | "system";

function applyTheme(mode: ThemeMode) {
  const dark =
    mode === "dark" ||
    (mode === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  localStorage.setItem("yal-theme", mode);
}

function ShellChrome({ children }: { children: React.ReactNode }) {
  const { tenant } = useParams({ strict: false }) as { tenant?: string };
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [theme, setTheme] = useState<ThemeMode>(
    () => (localStorage.getItem("yal-theme") as ThemeMode | null) ?? "system",
  );

  const tenantId = tenant ?? "default";

  const commands: CommandItem[] = useMemo(
    () => [
      { id: "catalog", label: t("nav.catalog"), to: "/$tenant/catalog" },
      { id: "runs", label: t("nav.runs"), to: "/$tenant/runs" },
      { id: "observe", label: t("nav.observe"), to: "/$tenant/observe" },
      { id: "settings", label: t("nav.settings"), to: "/$tenant/settings" },
      {
        id: "kitchen",
        label: "Kitchen sink",
        to: "/$tenant/dev/kitchen-sink",
      },
    ],
    [],
  );

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const nav = [
    { to: "/$tenant/catalog" as const, label: t("nav.catalog"), key: "catalog" },
    { to: "/$tenant/runs" as const, label: t("nav.runs"), key: "runs" },
    { to: "/$tenant/observe" as const, label: t("nav.observe"), key: "observe" },
    {
      to: "/$tenant/settings" as const,
      label: t("nav.settings"),
      key: "settings",
    },
  ];

  const crumb = pathname.replace(/^\//, "") || t("shell.breadcrumb.home");

  return (
    <div className={styles.shell}>
      <header className={styles.top}>
        <div>
          <Link
            to="/$tenant/runs"
            params={{ tenant: tenantId }}
            className={styles.brand}
          >
            YAL
          </Link>
          <span className={styles.tenant} title={t("shell.tenant")}>
            {tenantId}
          </span>
        </div>
        <div className={styles.actions}>
          <Button
            variant="ghost"
            type="button"
            onClick={() => setPaletteOpen(true)}
            aria-keyshortcuts="Meta+K"
          >
            ⌘K {t("shell.search")}
          </Button>
          <Menu
            trigger={
              <Button
                variant="secondary"
                type="button"
                aria-label={t("shell.theme")}
              >
                Theme
              </Button>
            }
            items={[
              {
                label: t("shell.theme.light"),
                onSelect: () => setTheme("light"),
              },
              {
                label: t("shell.theme.dark"),
                onSelect: () => setTheme("dark"),
              },
              {
                label: t("shell.theme.system"),
                onSelect: () => setTheme("system"),
              },
            ]}
          />
          <IconButton
            icon={theme === "dark" ? "sun" : "moon"}
            aria-label={t("shell.theme")}
            onClick={() => setTheme((m) => (m === "dark" ? "light" : "dark"))}
          />
          <Avatar name={t("shell.user")} />
        </div>
      </header>
      <div className={styles.body}>
        <nav className={styles.nav} aria-label="Primary">
          {nav.map((item) => (
            <Link
              key={item.key}
              to={item.to}
              params={{ tenant: tenantId }}
              data-active={pathname.includes(`/${item.key}`)}
            >
              <span className={styles.navLabel}>{item.label}</span>
            </Link>
          ))}
        </nav>
        <main className={styles.main}>
          <div className={styles.crumb}>{crumb}</div>
          {children}
        </main>
      </div>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        commands={commands}
        tenant={tenantId}
      />
    </div>
  );
}

export function ShellLayout() {
  return (
    <ToastProvider>
      <ShellChrome>
        <Outlet />
      </ShellChrome>
    </ToastProvider>
  );
}
