# Frontend Implementation Guide - Complete Reference

## Overview

This comprehensive guide covers React UI components, core utilities, routing, and build patterns for modern frontend applications.

---

# PART 1: UI COMPONENT LIBRARY

## Component Architecture Patterns

### 1. Radix UI Integration

All components wrap **Radix UI primitives** for accessibility:

```typescript
import * as RDialog from "@radix-ui/react-dialog";
import styles from "./Dialog.module.css";

export function Dialog({ open, onOpenChange, title, children, trigger }) {
  const rootProps: RDialog.DialogProps = {};
  if (open !== undefined) rootProps.open = open;
  if (onOpenChange !== undefined) rootProps.onOpenChange = onOpenChange;

  return (
    <RDialog.Root {...rootProps}>
      {trigger && <RDialog.Trigger asChild>{trigger}</RDialog.Trigger>}
      <RDialog.Portal>
        <RDialog.Overlay className={styles.overlay} />
        <RDialog.Content className={styles.content}>
          {title && <RDialog.Title className={styles.title}>{title}</RDialog.Title>}
          {children}
          <RDialog.Close className={styles.close}>×</RDialog.Close>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}
```

### 2. CSS Modules Pattern

```typescript
import { cx } from "./lib/cx";
import styles from "./Button.module.css";

export function Button({ variant = "primary", loading, disabled, className, ...rest }) {
  return (
    <button
      className={cx(
        styles.root,
        styles[variant],
        loading && styles.loading,
        disabled && styles.disabled,
        className
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Spinner size="sm" /> : null}
      {children}
    </button>
  );
}
```

**CSS Module**:
```css
.root {
  display: inline-flex;
  align-items: center;
  gap: var(--density-gap);
  min-height: var(--density-row-height);
  padding: var(--density-padding-y) var(--space-4);
  border-radius: var(--radius-md);
  font-family: var(--font-body);
  transition: background var(--motion-fast) var(--motion-ease);
}

.primary {
  background: var(--color-accent-default);
  color: var(--color-text-inverse);
}

.primary:hover:not(:disabled) {
  background: var(--color-accent-hover);
}
```

### 3. Slot Pattern (Polymorphic)

```typescript
import { Slot } from "@radix-ui/react-slot";

export function Button({ asChild, ...props }) {
  const Comp = asChild ? Slot : "button";
  return <Comp {...props} />;
}

// Usage:
<Button asChild>
  <Link to="/path">Navigate</Link>
</Button>
```

## Key Components

### Button Component

```typescript
type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
  asChild?: boolean;
}

export function Button({ variant = "primary", loading, ...props }: ButtonProps) {
  const Comp = props.asChild ? Slot : "button";
  return (
    <Comp
      className={cx(styles.root, styles[variant], loading && styles.loading)}
      disabled={props.disabled || loading}
      {...props}
    >
      {loading && <Spinner size="sm" />}
      {props.children}
    </Comp>
  );
}
```

### Input Component

```typescript
interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
}

export function Input({ label, error, id, className, ...rest }: InputProps) {
  const inputId = id || useId();

  const input = (
    <input
      id={inputId}
      className={cx(styles.control, error && styles.error, className)}
      aria-invalid={error ? "true" : undefined}
      aria-describedby={error ? `${inputId}-error` : undefined}
      {...rest}
    />
  );

  if (!label && !error) return input;

  return (
    <label className={styles.field} htmlFor={inputId}>
      {label && <span className={styles.label}>{label}</span>}
      {input}
      {error && (
        <span id={`${inputId}-error`} className={styles.errorText}>
          {error}
        </span>
      )}
    </label>
  );
}
```

### Dialog Component

```typescript
export function Dialog({ open, onOpenChange, title, description, children, trigger }) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      {trigger && <RDialog.Trigger asChild>{trigger}</RDialog.Trigger>}

      <RDialog.Portal>
        <RDialog.Overlay className={styles.overlay} />

        <RDialog.Content className={styles.content}>
          <div className={styles.header}>
            {title && <RDialog.Title>{title}</RDialog.Title>}
            {description && <RDialog.Description>{description}</RDialog.Description>}
            <RDialog.Close className={styles.close}>
              <Icon name="x" />
            </RDialog.Close>
          </div>

          <div className={styles.body}>{children}</div>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}
```

### Toast System

```typescript
// Context provider
export function ToastProvider({ children }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((toast: Omit<ToastItem, "id">) => {
    const id = crypto.randomUUID();
    setItems(prev => [...prev, { ...toast, id }]);

    // Auto-dismiss after duration
    setTimeout(() => {
      setItems(prev => prev.filter(t => t.id !== id));
    }, toast.duration || 5000);
  }, []);

  const remove = useCallback((id: string) => {
    setItems(prev => prev.filter(t => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ push, remove }}>
      {children}
      <div className={styles.toastContainer}>
        {items.map(item => (
          <Toast key={item.id} {...item} onClose={() => remove(item.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// Hook
export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used within ToastProvider");
  return context;
}

// Usage
const { push } = useToast();
push({ title: "Success", description: "Item saved" });
```

### ErrorState Component

```typescript
export function ErrorState({ error, onRetry, action }: ErrorStateProps) {
  const copy = useMemo(() => {
    switch (error.kind) {
      case "network":
        return { title: "Network Error", body: "Check connection and try again" };
      case "auth":
        return { title: "Authentication Required", body: "Please sign in" };
      case "permission":
        return { title: "Permission Denied", body: `Missing: ${error.missing}` };
      default:
        return { title: "Something Went Wrong", body: "Please try again" };
    }
  }, [error]);

  return (
    <div className={styles.errorState} role="alert">
      <Icon name="alert-circle" size={48} />
      <h3>{copy.title}</h3>
      <p>{copy.body}</p>
      {onRetry && <Button onClick={onRetry}>Retry</Button>}
      {action}
    </div>
  );
}
```

---

# PART 2: CORE UTILITIES

## HTTP Transport Layer

### Request Client

```typescript
export async function request<T>(path: string, init: RequestOptions = {}): Promise<T> {
  const {
    timeoutMs = 30_000,
    retries = 2,
    headers: initHeaders,
    signal: outerSignal,
    ...rest
  } = init;

  const requestId = crypto.randomUUID();
  let attempt = 0;

  while (attempt <= retries) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const headers = new Headers(initHeaders);
      headers.set("X-Request-Id", requestId);
      headers.set("Accept", "application/json");

      const response = await fetch(path, {
        ...rest,
        credentials: "include",
        headers,
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (!response.ok) {
        const body = await parseBody(response);
        const error = mapHttpError(response.status, body, requestId);

        // Retry on 429 or 5xx
        if (attempt < retries && isRetryable(response.status)) {
          await sleep(BASE_BACKOFF_MS * 2 ** attempt);  // Exponential backoff
          attempt++;
          continue;
        }

        throw new TransportError(error);
      }

      const data = await parseBody(response);
      return data as T;

    } catch (err) {
      clearTimeout(timeout);

      if (err instanceof TransportError) throw err;

      // Network error - retry
      if (attempt < retries) {
        await sleep(BASE_BACKOFF_MS * 2 ** attempt);
        attempt++;
        continue;
      }

      throw new TransportError({ kind: "network", retryable: true });
    }
  }

  throw new TransportError({ kind: "network", retryable: false });
}
```

### Error Mapping

```typescript
export function mapHttpError(status: number, body: unknown, requestId?: string): AppError {
  const data = asRecord(body);

  switch (status) {
    case 401:
      return { kind: "auth", action: "reauthenticate" };
    case 403:
      return { kind: "permission", missing: data.missing ?? "unknown" };
    case 404:
      return { kind: "notFound", resource: data.resource ?? "unknown" };
    case 422:
      return { kind: "validation", fields: data.fields ?? [] };
    case 429:
      return { kind: "rateLimit", retryAfter: data.retryAfter ?? 0 };
    case 400:
      if (data.code === "BUDGET_EXCEEDED") {
        return { kind: "budgetExceeded", limit: data.limit, current: data.current };
      }
      return { kind: "validation", fields: [] };
    default:
      if (status >= 500) {
        return { kind: "server", requestId, retryable: true };
      }
      return { kind: "server", retryable: false };
  }
}
```

## Formatters

### Duration Formatter

```typescript
export function formatDuration(ms: number, options: DurationOptions = {}): string {
  const { precision = "auto" } = options;

  if (ms < 1000) return `${ms}ms`;

  const seconds = Math.floor((ms / 1000) % 60);
  const minutes = Math.floor((ms / 60_000) % 60);
  const hours = Math.floor((ms / 3_600_000) % 24);
  const days = Math.floor(ms / 86_400_000);

  const parts: string[] = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0) parts.push(`${hours}h`);
  if (minutes > 0) parts.push(`${minutes}m`);
  if (seconds > 0 || parts.length === 0) parts.push(`${seconds}s`);

  if (precision === "coarse" && parts.length > 2) {
    return parts.slice(0, 2).join(" ");
  }

  return parts.join(" ");
}

// Examples:
formatDuration(500)         // "500ms"
formatDuration(90_000)      // "1m 30s"
formatDuration(3_723_000)   // "1h 2m 3s"
```

### Timestamp Formatter

```typescript
export function formatTimestamp(ms: number, options: TimestampOptions): string {
  const { locale = "en-US", timeZone, dateStyle = "medium", timeStyle = "medium" } = options;

  // timeZone is REQUIRED - never rely on browser default
  if (!timeZone) {
    throw new Error("timeZone is required");
  }

  return new Intl.DateTimeFormat(locale, {
    dateStyle,
    timeStyle,
    timeZone,
  }).format(new Date(ms));
}

// Usage:
formatTimestamp(Date.now(), { timeZone: "America/New_York" })
// "Jan 15, 2025, 3:45:30 PM"
```

### Cost Formatter

```typescript
export function formatCost(amount: number, options: CostOptions): string {
  const { locale = "en-US", currency = "USD" } = options;

  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(amount);
}

// Usage:
formatCost(0.0023, { currency: "USD" })  // "$0.0023"
formatCost(150.50, { currency: "EUR", locale: "de-DE" })  // "150,50 €"
```

## i18n System

### Catalog

```typescript
export const catalog = {
  // Navigation
  "nav.catalog": "Catalog",
  "nav.runs": "Runs",
  "nav.observe": "Observe",
  "nav.settings": "Settings",

  // Errors
  "error.network": "Network error. Check your connection and try again.",
  "error.auth": "Authentication required. Please sign in.",
  "error.permission": "You need {missing} permission for this action.",
  "error.validation": "Please correct the errors and try again.",

  // Actions
  "action.save": "Save",
  "action.cancel": "Cancel",
  "action.delete": "Delete",
  "action.retry": "Retry",
} as const;

export type CatalogKey = keyof typeof catalog;
```

### Translation Function

```typescript
export function t(key: CatalogKey, vars?: Record<string, string | number>): string {
  const template = catalog[key];

  if (!vars) return template;

  // Replace {placeholder} syntax
  return template.replace(/\{(\w+)\}/g, (match, name) => {
    const value = vars[name];
    return value === undefined ? match : String(value);
  });
}

// Usage:
t("nav.catalog")  // "Catalog"
t("error.permission", { missing: "runs.write" })  // "You need runs.write permission..."
```

## Permissions System

```typescript
export type User = {
  id: string;
  roles?: readonly string[];
};

export type Permission = { allowed: true } | { allowed: false; reason: string };

// Phase 1 stub - always allows
export function can(user: User, action: string, resource?: Resource): Permission {
  // In Phase 2, implement real RBAC:
  // - Check user.roles against resource permissions
  // - Support hierarchical permissions
  // - Cache permission checks
  return { allowed: true };
}

// Usage:
const permission = can(currentUser, "runs.delete", { id: "123", type: "run" });
if (!permission.allowed) {
  toast.error(permission.reason);
}
```

---

# PART 3: ROUTING AND APPLICATION STRUCTURE

## TanStack Router Setup

### Router Configuration

```typescript
import { createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/$tenant/runs", params: { tenant: "default" } });
  },
});

const tenantRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/$tenant",
  component: ShellLayout,
});

const runsRoute = createRoute({
  getParentRoute: () => tenantRoute,
  path: "/runs",
  component: RunsPage,
});

const runDetailRoute = createRoute({
  getParentRoute: () => tenantRoute,
  path: "/runs/$runId",
  component: RunDetailPage,
  loader: ({ params }) => fetchRun(params.runId),
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  tenantRoute.addChildren([
    runsRoute,
    runDetailRoute,
    catalogRoute,
    observeRoute,
    settingsRoute,
  ]),
]);

export const router = createRouter({ routeTree });

// Type safety
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
```

### Shell Layout

```typescript
export function ShellLayout() {
  const { tenant } = useParams({ strict: false });
  const pathname = useRouterState(s => s.location.pathname);
  const navigate = useNavigate();

  // Command palette state
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Theme state
  const [theme, setTheme] = useState<ThemeMode>(() =>
    (localStorage.getItem("theme") as ThemeMode) || "system"
  );

  // Cmd+K shortcut
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(v => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("theme", next);
    document.documentElement.setAttribute("data-theme", next);
  };

  return (
    <ToastProvider>
      <div className={styles.shell}>
        <header className={styles.header}>
          <Link to="/$tenant/runs" params={{ tenant }}>
            <Logo />
          </Link>

          <span className={styles.tenant}>{tenant}</span>

          <div className={styles.actions}>
            <IconButton
              icon="search"
              onClick={() => setPaletteOpen(true)}
              aria-label="Search (Cmd+K)"
              aria-keyshortcuts="Meta+K"
            />
            <IconButton
              icon={theme === "dark" ? "sun" : "moon"}
              onClick={toggleTheme}
              aria-label="Toggle theme"
            />
            <Avatar name="User" />
          </div>
        </header>

        <div className={styles.body}>
          <nav className={styles.nav}>
            {navItems.map(item => (
              <Link
                key={item.key}
                to={item.to}
                params={{ tenant }}
                className={styles.navLink}
                data-active={pathname.includes(item.key)}
              >
                <Icon name={item.icon} />
                <span>{t(item.labelKey)}</span>
              </Link>
            ))}
          </nav>

          <main className={styles.main}>
            <Outlet />
          </main>
        </div>

        <CommandPalette
          open={paletteOpen}
          onOpenChange={setPaletteOpen}
          tenant={tenant}
        />
      </div>
    </ToastProvider>
  );
}
```

### Command Palette

```typescript
export function CommandPalette({ open, onOpenChange, tenant }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const navigate = useNavigate();

  const commands = useMemo(() => [
    { id: "runs", label: "View Runs", to: "/$tenant/runs", icon: "list" },
    { id: "catalog", label: "Browse Catalog", to: "/$tenant/catalog", icon: "book" },
    { id: "settings", label: "Settings", to: "/$tenant/settings", icon: "settings" },
  ], []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return commands;
    return commands.filter(c => c.label.toLowerCase().includes(needle));
  }, [commands, query]);

  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onOpenChange(false);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex(i => Math.min(i + 1, filtered.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex(i => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        const cmd = filtered[activeIndex];
        if (cmd) {
          navigate({ to: cmd.to, params: { tenant } });
          onOpenChange(false);
        }
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, filtered, activeIndex, tenant, navigate, onOpenChange]);

  if (!open) return null;

  return (
    <div className={styles.overlay} onClick={() => onOpenChange(false)}>
      <div className={styles.palette} onClick={e => e.stopPropagation()}>
        <input
          autoFocus
          value={query}
          onChange={e => { setQuery(e.target.value); setActiveIndex(0); }}
          placeholder="Search commands..."
        />

        <ul>
          {filtered.map((cmd, i) => (
            <li key={cmd.id}>
              <button
                data-active={i === activeIndex}
                onMouseEnter={() => setActiveIndex(i)}
                onClick={() => {
                  navigate({ to: cmd.to, params: { tenant } });
                  onOpenChange(false);
                }}
              >
                <Icon name={cmd.icon} />
                <span>{cmd.label}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
```

---

# PART 4: BUILD AND DEVELOPMENT

## Vite Configuration

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },

  server: {
    port: 5173,
    open: false,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },

  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ["react", "react-dom"],
          router: ["@tanstack/react-router"],
          query: ["@tanstack/react-query"],
        },
      },
    },
  },

  css: {
    modules: {
      localsConvention: "camelCaseOnly",
    },
  },
});
```

## ESLint Configuration

```javascript
import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import boundaries from "eslint-plugin-boundaries";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["**/node_modules/**", "**/dist/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  prettier,

  // Architecture boundaries
  {
    plugins: { boundaries },
    settings: {
      "boundaries/elements": [
        { type: "tokens", pattern: "packages/tokens/*" },
        { type: "core", pattern: "packages/core/*" },
        { type: "ui", pattern: "packages/ui/*" },
        { type: "features", pattern: "packages/features/*" },
        { type: "app", pattern: "packages/app-web/*" },
      ],
    },
    rules: {
      "boundaries/element-types": [
        "error",
        {
          default: "disallow",
          rules: [
            { from: "tokens", allow: [] },
            { from: "core", allow: ["core", "tokens"] },
            { from: "ui", allow: ["ui", "core", "tokens"] },
            { from: "features", allow: ["features", "ui", "core", "tokens"] },
            { from: "app", allow: ["*"] },
          ],
        },
      ],
    },
  },

  // Core must stay React-free
  {
    files: ["packages/core/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            { name: "react", message: "Core must stay rendering-free (no React)." },
          ],
          patterns: [{ group: ["*.css"], message: "Core cannot import CSS." }],
        },
      ],
    },
  },
);
```

## Testing

### Unit Tests

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatDuration } from "./duration";

test("formatDuration", () => {
  assert.equal(formatDuration(500), "500ms");
  assert.equal(formatDuration(1000), "1s");
  assert.equal(formatDuration(90_000), "1m 30s");
  assert.equal(formatDuration(3_723_000), "1h 2m 3s");
});
```

### Component Testing with Ladle

```typescript
// Button.stories.tsx
import type { Story } from "@ladle/react";
import { Button } from "./Button";

export const Primary: Story = () => <Button variant="primary">Primary</Button>;
export const Secondary: Story = () => <Button variant="secondary">Secondary</Button>;
export const Loading: Story = () => <Button loading>Loading</Button>;
export const Disabled: Story = () => <Button disabled>Disabled</Button>;
```

---

# PART 5: BEST PRACTICES

## Component Design

1. **Single Responsibility** - One component, one job
2. **Composition over Inheritance** - Build complex from simple
3. **Type Safety** - Full TypeScript coverage
4. **Accessibility** - WCAG AA compliance

## File Organization

```
Component/
├── Component.tsx
├── Component.module.css
├── Component.stories.tsx  (optional)
└── Component.test.tsx     (optional)
```

## Styling

1. **Use Tokens** - Always use CSS custom properties
2. **Mobile First** - Write mobile styles first
3. **Avoid Magic Numbers** - Use spacing scale
4. **Semantic Names** - `--color-surface-base`, not `--gray-50`

## Performance

1. **Code Splitting** - Route-based lazy loading
2. **Memoization** - `useMemo`, `useCallback` judiciously
3. **List Virtualization** - For long lists
4. **Debounce/Throttle** - For expensive operations

## Accessibility

1. **Semantic HTML** - Use proper elements
2. **ARIA Labels** - On icon buttons
3. **Keyboard Navigation** - Tab, Enter, Escape
4. **Focus Management** - Visible focus indicators

---

# Conclusion

This guide provides comprehensive patterns for building production-ready React applications with:

- **Type-safe** components and utilities
- **Accessible** UI following WCAG guidelines
- **Performant** builds with code splitting
- **Maintainable** architecture with clear boundaries
- **Developer-friendly** tooling and conventions

Apply these patterns to create scalable, robust frontend applications.
