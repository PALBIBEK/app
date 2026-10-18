# Frontend Overview and Architecture

## Overview

This document provides a comprehensive overview of a production-grade React frontend architecture built as a monorepo using modern web technologies and best practices.

## Architecture Philosophy

### Core Principles

1. **Layered Monorepo Architecture**
   - Strict separation of concerns enforced by ESLint boundaries
   - Pure utility layer (no UI dependencies)
   - Reusable component library
   - Application-specific features

2. **Design System First**
   - Token-based design system (Style Dictionary)
   - Semantic tokens for theme-ability
   - Consistent spacing, typography, colors
   - Light/dark mode support

3. **Type Safety Throughout**
   - TypeScript strict mode enabled
   - No `any` types
   - Type-safe routing with TanStack Router
   - Branded types for domain concepts

4. **Component Composition**
   - Radix UI headless primitives
   - CSS Modules for styling
   - Compound component pattern
   - Slot pattern for polymorphic components

5. **Progressive Enhancement**
   - Phase-based development (0 → 1 → 2 → 5)
   - Feature flags for experimental features
   - Graceful degradation
   - Anti-FOUC (Flash of Unstyled Content)

## Technology Stack

### Core Framework

**React 19.2.8**
- Latest with concurrent features
- Automatic batching
- `use()` hook for promises
- Improved Suspense boundaries

**TypeScript 5.8.3**
- Strict mode enabled
- `noUncheckedIndexedAccess`
- `exactOptionalPropertyTypes`
- `verbatimModuleSyntax`

### Routing & State

**TanStack Router**
- Type-safe file-based routing
- Automatic code splitting
- Nested layouts
- Type-safe route parameters

**TanStack Query**
- Server state management
- Automatic caching and revalidation
- Optimistic updates
- Request deduplication

### Build Tools

**Vite 6.3.3**
- Fast HMR (Hot Module Replacement)
- Optimized production builds
- Native ESM in development
- CSS code splitting

**pnpm 9.15.9**
- Fast, disk-space efficient
- Strict dependency resolution
- Workspace support
- Deterministic installs

### UI & Styling

**Radix UI**
- Headless, accessible primitives
- 40+ components
- Unstyled, fully customizable
- WAI-ARIA compliant

**CSS Modules**
- Scoped styles
- No runtime overhead
- Type-safe class names
- Tree-shakeable

**Style Dictionary**
- Token generation
- Multi-platform output (CSS, JSON)
- Semantic token system
- Theme transformations

### Code Quality

**ESLint + Prettier**
- Flat config format
- Architectural boundaries enforcement
- TypeScript-aware linting
- Auto-formatting

## Monorepo Structure

```
frontend/
├── packages/
│   ├── tokens/      # Design system tokens
│   ├── core/        # Framework-agnostic utilities
│   ├── ui/          # UI component library
│   ├── features/    # Domain features (Phase 2+)
│   └── app-web/     # Main application
├── contracts/       # API type definitions
└── scripts/         # Test & CI scripts
```

### Package Dependency Graph

```
tokens (standalone)
  ↓
core → tokens
  ↓
ui → core, tokens
  ↓
features → ui, core, tokens
  ↓
app-web → features, ui, core, tokens
```

**Enforced Rules:**
- `tokens`: No dependencies (pure design tokens)
- `core`: Cannot import React or CSS (pure utilities)
- `ui`: Can use React but not domain logic
- `features`: Domain-specific UI modules
- `app-web`: Can import everything

## Package Purposes

### @yal/tokens

**Purpose**: Design system foundation

**Contents**:
- Primitive tokens (colors, spacing, typography)
- Semantic tokens (light/dark themes)
- CSS custom properties generation
- Theme modes (light, dark, system)
- Density modes (comfortable, compact)

**Key Files**:
```
tokens/
├── src/
│   ├── primitive/base.json
│   ├── semantic/light.json
│   └── semantic/dark.json
├── style-dictionary.config.mjs
└── dist/tokens.css
```

### @yal/core

**Purpose**: Framework-agnostic utilities

**Contents**:
- HTTP transport layer
- Error mapping and handling
- Formatters (duration, timestamp, cost, tokens)
- i18n system (catalog + translation)
- Permissions system (RBAC)
- Query key generation

**Constraints**:
- NO React imports allowed
- NO CSS imports allowed
- Pure TypeScript utilities only

**Key Exports**:
```typescript
export { request, type AppError } from "./transport";
export { formatDuration, formatTimestamp, formatCost } from "./format";
export { t, type CatalogKey } from "./i18n";
export { can, type Permission } from "./permissions";
export { queryKeyFromRoute } from "./query";
```

### @yal/ui

**Purpose**: Reusable UI component library

**Contents**:
- 40+ Radix UI-based components
- CSS Modules for styling
- Loading states (Spinner, Skeleton)
- Empty/Error states
- Toast notifications
- Icon system (Lucide icons)

**Patterns**:
- Compound components (Dialog.Root, Dialog.Content)
- Controlled/Uncontrolled variants
- Slot pattern for polymorphic components
- Consistent API surface

**Key Exports**:
```typescript
export { Button, IconButton } from "./Button";
export { Input, Textarea } from "./Input";
export { Dialog, Drawer } from "./Dialog";
export { Tooltip } from "./Tooltip";
export { EmptyState, ErrorState } from "./states";
export { ToastProvider, useToast } from "./Toast";
```

### @yal/features

**Purpose**: Domain-specific feature modules

**Status**: Empty (Phase 2+)

**Planned**:
- Session list feature
- Event viewer feature
- Catalog browser feature
- Observability dashboard

### @yal/app-web

**Purpose**: Main application shell

**Contents**:
- TanStack Router setup
- Shell layout (header, sidebar, footer)
- Command palette (Cmd+K)
- Page components
- API integration
- Authentication flow

**Key Files**:
```typescript
router.tsx           // Route definitions
ShellLayout.tsx      // Application chrome
CommandPalette.tsx   // Global search
main.tsx             // Entry point
```

## Architectural Patterns

### Layered Architecture

```
┌──────────────────────────────────┐
│  Application Layer (app-web)     │ - Routes, pages, auth
│                                  │ - Integration
└──────────────┬───────────────────┘
               │
┌──────────────▼───────────────────┐
│  Feature Layer (features)        │ - Domain modules
│                                  │ - Composed UI
└──────────────┬───────────────────┘
               │
┌──────────────▼───────────────────┐
│  Component Layer (ui)            │ - Reusable components
│                                  │ - Design system
└──────────────┬───────────────────┘
               │
┌──────────────▼───────────────────┐
│  Utility Layer (core)            │ - Pure functions
│                                  │ - No React/CSS
└──────────────┬───────────────────┘
               │
┌──────────────▼───────────────────┐
│  Token Layer (tokens)            │ - Design tokens
│                                  │ - CSS variables
└──────────────────────────────────┘
```

### Separation of Concerns

**Rendering vs Logic**:
- `@yal/core`: Pure logic, no rendering
- `@yal/ui`: Pure presentation, no business logic
- `@yal/features`: Combined domain logic + UI
- `@yal/app-web`: Application integration

**Data vs UI**:
- TanStack Query manages server state
- React state for UI-only concerns
- No global state library needed
- Props for component communication

### Error Handling Strategy

**Typed Errors**:
```typescript
type AppError =
  | { kind: "network"; retryable: true }
  | { kind: "auth"; action: "reauthenticate" }
  | { kind: "permission"; missing: string }
  | { kind: "validation"; fields: FieldError[] }
  | { kind: "notFound"; resource: string }
  | { kind: "rateLimit"; retryAfter: number }
  | { kind: "server"; requestId?: string; retryable: true };
```

**Error Boundaries**:
- Per-route error boundaries
- Global fallback
- Retry mechanisms
- User-friendly messages

### Theme System

**Three Theme Modes**:
1. **Light** - High contrast, professional
2. **Dark** - Reduced eye strain, OLED-friendly
3. **System** - Follows OS preference

**Implementation**:
```typescript
// Applied in HTML before first render (anti-FOUC)
const stored = localStorage.getItem("theme") || "system";
const dark = stored === "dark" ||
  (stored === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
```

**CSS Usage**:
```css
:root,
[data-theme="light"] {
  --color-surface-base: #f7faf8;
  --color-text-primary: #14201c;
}

[data-theme="dark"] {
  --color-surface-base: #0c1411;
  --color-text-primary: #f7faf8;
}
```

### Density System

**Two Density Modes**:
1. **Comfortable** (default) - 44px rows, 12px padding
2. **Compact** - 32px rows, 6px padding

**Usage**:
```css
[data-density="comfortable"] {
  --density-row-height: 44px;
  --density-padding-y: 12px;
  --density-gap: 12px;
}

[data-density="compact"] {
  --density-row-height: 32px;
  --density-padding-y: 6px;
  --density-gap: 8px;
}
```

## Development Phases

### Phase 0: Scaffold ✅

**Goals**:
- Monorepo setup
- Package structure
- Build tooling
- Basic routing

**Deliverables**:
- pnpm workspace
- TypeScript configuration
- Vite setup
- ESLint + Prettier

### Phase 1: Foundation ✅

**Goals**:
- Design system tokens
- Core utilities
- UI component library
- Shell layout

**Deliverables**:
- `@yal/tokens` with light/dark themes
- `@yal/core` with transport, formatters, i18n
- `@yal/ui` with 40+ components
- Shell layout with navigation

### Phase 2: Features 🏗️

**Goals**:
- Session list page
- Event viewer (SSE)
- Catalog browser
- Real-time updates

**Planned Components**:
- `SessionList` with filtering
- `EventStream` with auto-scroll
- `CatalogGrid` with search
- `SessionDetail` with timeline

### Phase 5: Operations 🔮

**Goals**:
- Archive/restore UI
- Metrics dashboard
- Observability views
- Admin settings

## API Integration Pattern

### Same-Origin Strategy

**Development**:
```
Browser → /api/v1/sessions
  ↓ Vite proxy
Backend ← /v1/sessions (strips /api)
```

**Production**:
```
Browser → /api/v1/sessions
  ↓ Nginx/CDN
Backend ← /v1/sessions
```

**Benefits**:
- No CORS issues
- Simpler auth (cookies)
- Consistent URLs

### Request Pattern

```typescript
import { request } from "@yal/core";

// Automatic retry, timeout, error mapping
const sessions = await request<Session[]>("/api/v1/sessions", {
  method: "GET",
  timeoutMs: 30_000,
  retries: 2,
});
```

### Query Integration

```typescript
import { useQuery } from "@tanstack/react-query";
import { queryKeyFromRoute } from "@yal/core";

function useSessions() {
  return useQuery({
    queryKey: queryKeyFromRoute("/sessions"),
    queryFn: () => request<Session[]>("/api/v1/sessions"),
    staleTime: 30_000,
  });
}
```

## Accessibility Features

### Keyboard Navigation

- **Cmd/Ctrl+K**: Command palette
- **Escape**: Close modals/dialogs
- **Arrow keys**: Navigate lists
- **Enter**: Select/submit
- **Tab**: Focus management

### Screen Reader Support

- Semantic HTML (`<nav>`, `<main>`, `<button>`)
- ARIA labels on icon buttons
- ARIA live regions for dynamic content
- Focus indicators on all interactive elements

### Motion Reduction

```css
@media (prefers-reduced-motion: reduce) {
  * {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

### Color Contrast

- WCAG AA compliant
- 4.5:1 minimum for text
- 3:1 minimum for UI components
- Tested in light and dark modes

## Performance Optimizations

### Code Splitting

```typescript
// Automatic route-based splitting
const CatalogPage = lazy(() => import("./CatalogPage"));
```

### Asset Optimization

- Vite optimizes images
- CSS is code-split per route
- Tree-shaking removes unused code
- Minification in production

### Caching Strategy

```typescript
// TanStack Query caching
{
  staleTime: 30_000,     // Data fresh for 30s
  cacheTime: 300_000,    // Keep in cache for 5min
  refetchOnWindowFocus: true,
}
```

### Bundle Size

**Production builds**:
- Main bundle: ~150KB (gzipped)
- Vendor bundle: ~200KB (gzipped)
- Route chunks: 10-50KB each

## Best Practices

### Component Design

1. **Single Responsibility** - One component, one job
2. **Composition over Inheritance** - Build complex from simple
3. **Props over State** - Prefer controlled components
4. **Type Safety** - Full TypeScript coverage

### File Organization

```
Component.tsx          // Component implementation
Component.module.css   // Scoped styles
Component.stories.tsx  // Ladle stories (optional)
Component.test.tsx     // Unit tests (optional)
```

### Import Conventions

```typescript
// 1. External dependencies
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

// 2. Internal workspace packages
import { Button } from "@yal/ui";
import { formatDuration } from "@yal/core";

// 3. Relative imports
import { useSession } from "./hooks";
import styles from "./Component.module.css";
```

### CSS Best Practices

1. **Use Tokens**: Always use CSS custom properties
2. **Mobile First**: Write mobile styles first, desktop in media queries
3. **Avoid Magic Numbers**: Use spacing scale (`--space-N`)
4. **Semantic Names**: `--color-surface-base`, not `--color-gray-50`

## Testing Strategy

### Unit Tests

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatDuration } from "./duration";

test("formatDuration", () => {
  assert.equal(formatDuration(500), "500ms");
  assert.equal(formatDuration(90_000), "1m 30s");
});
```

### Component Tests

**Ladle for visual testing**:
```bash
pnpm --filter @yal/ui ladle
```

### Integration Tests

**Phase validation scripts**:
- `phase0-scaffold.test.mjs` - Monorepo structure
- `phase1-foundation.test.mjs` - Tokens, core, UI
- `phase2-runs.test.mjs` - Feature completeness

## Deployment

### Build Command

```bash
# Build all packages
pnpm build

# Build specific package
pnpm --filter @yal/app-web build
```

### Output Structure

```
packages/app-web/dist/
├── index.html
├── assets/
│   ├── index-[hash].js
│   ├── index-[hash].css
│   └── vendor-[hash].js
└── [route-chunks]/
```

### Environment Variables

```bash
# Development
VITE_FLAG_BUILDER=false
VITE_FLAG_KITCHEN_SINK=true

# Production
VITE_FLAG_BUILDER=true
VITE_FLAG_KITCHEN_SINK=false
```

## Conclusion

This frontend architecture demonstrates:

- **Production-grade** patterns and tooling
- **Type safety** throughout the stack
- **Accessibility** first approach
- **Performance** optimizations
- **Developer experience** with HMR, Ladle, TypeScript

The monorepo structure with strict boundaries ensures scalability, maintainability, and code quality as the application grows.
