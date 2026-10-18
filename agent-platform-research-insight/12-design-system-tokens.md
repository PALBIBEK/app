# Design System and Tokens

## Overview

This document covers the design system foundation built with Style Dictionary, including token architecture, theme system, and CSS custom properties generation.

## Token Architecture

### Two-Tier System

**Tier 1: Primitive Tokens** - Raw design values
- Colors (50-950 scale)
- Spacing (1-8 scale)
- Typography (font families, sizes)
- Border radius
- Motion timing
- Shadows

**Tier 2: Semantic Tokens** - Purpose-driven aliases
- Surface colors (base, raised, sunken)
- Text colors (primary, secondary, disabled)
- Accent colors (default, hover, pressed)
- Status colors (info, success, warning, danger)
- Border colors (subtle, strong, focus)

### File Structure

```
tokens/
├── src/
│   ├── primitive/
│   │   └── base.json          # Raw values
│   ├── semantic/
│   │   ├── light.json         # Light theme
│   │   └── dark.json          # Dark theme
│   └── index.ts
├── style-dictionary.config.mjs
├── package.json
└── dist/
    ├── tokens.css             # Generated CSS
    └── tokens.json            # JSON output
```

## Primitive Tokens

### Color System

**Neutral Palette** (50-950):
```json
{
  "color": {
    "neutral": {
      "0": { "value": "#ffffff" },
      "50": { "value": "#f7faf8" },
      "100": { "value": "#eef3f0" },
      "200": { "value": "#dce7e1" },
      "300": { "value": "#b8cdc4" },
      "400": { "value": "#95b4a7" },
      "500": { "value": "#3d4a44" },
      "600": { "value": "#2e3936" },
      "700": { "value": "#232d29" },
      "800": { "value": "#14201c" },
      "900": { "value": "#0c1411" },
      "950": { "value": "#070c0a" }
    }
  }
}
```

**Accent Palette** (Green):
```json
{
  "color": {
    "green": {
      "50": { "value": "#eef8f4" },
      "100": { "value": "#d8efe9" },
      "200": { "value": "#b1ded2" },
      "300": { "value": "#7ac8b5" },
      "400": { "value": "#4aa693" },
      "500": { "value": "#318c7a" },
      "600": { "value": "#0f6b5c" },
      "700": { "value": "#0b5850" },
      "800": { "value": "#0a4942" },
      "900": { "value": "#093d37" },
      "950": { "value": "#041a15" }
    }
  }
}
```

**Status Colors**:
```json
{
  "color": {
    "amber": {
      "100": { "value": "#f5e6d0" },
      "500": { "value": "#9a5b12" }
    },
    "red": {
      "100": { "value": "#f8e2e2" },
      "500": { "value": "#9b2c2c" }
    },
    "blue": {
      "100": { "value": "#dce8f5" },
      "500": { "value": "#1d4f91" }
    },
    "ok": {
      "100": { "value": "#dcefe3" },
      "500": { "value": "#1f6b3a" }
    }
  }
}
```

### Spacing Scale

**4px base unit**:
```json
{
  "space": {
    "1": { "value": "4px" },
    "2": { "value": "8px" },
    "3": { "value": "12px" },
    "4": { "value": "16px" },
    "5": { "value": "20px" },
    "6": { "value": "24px" },
    "7": { "value": "28px" },
    "8": { "value": "32px" }
  }
}
```

### Typography

**Font Stacks**:
```json
{
  "font": {
    "display": {
      "value": "Bricolage Grotesque, Source Sans 3, system-ui, sans-serif"
    },
    "body": {
      "value": "Source Sans 3, system-ui, sans-serif"
    },
    "mono": {
      "value": "IBM Plex Mono, ui-monospace, monospace"
    }
  }
}
```

**Type Scale** (fluid sizing):
```json
{
  "text": {
    "display": { "value": "clamp(1.75rem, 2.5vw, 2.25rem)" },
    "h1": { "value": "clamp(1.5rem, 2vw, 2rem)" },
    "h2": { "value": "clamp(1.25rem, 1.75vw, 1.75rem)" },
    "h3": { "value": "clamp(1.125rem, 1.5vw, 1.5rem)" },
    "body": { "value": "1rem" },
    "small": { "value": "0.875rem" },
    "tiny": { "value": "0.75rem" }
  }
}
```

### Border Radius

```json
{
  "radius": {
    "sm": { "value": "6px" },
    "md": { "value": "8px" },
    "lg": { "value": "10px" },
    "full": { "value": "9999px" }
  }
}
```

### Motion

```json
{
  "motion": {
    "fast": { "value": "120ms" },
    "base": { "value": "200ms" },
    "slow": { "value": "300ms" },
    "ease": { "value": "cubic-bezier(0.4, 0.0, 0.2, 1)" }
  }
}
```

## Semantic Tokens

### Light Theme

**Surface Colors**:
```json
{
  "color": {
    "surface": {
      "base": { "value": "{color.neutral.50}" },      // #f7faf8
      "raised": { "value": "{color.neutral.0}" },     // #ffffff
      "sunken": { "value": "{color.neutral.100}" }    // #eef3f0
    }
  }
}
```

**Text Colors**:
```json
{
  "color": {
    "text": {
      "primary": { "value": "{color.neutral.800}" },     // #14201c
      "secondary": { "value": "{color.neutral.500}" },   // #3d4a44
      "disabled": { "value": "{color.neutral.400}" },    // #95b4a7
      "inverse": { "value": "{color.neutral.0}" }        // #ffffff
    }
  }
}
```

**Accent Colors**:
```json
{
  "color": {
    "accent": {
      "default": { "value": "{color.green.600}" },   // #0f6b5c
      "hover": { "value": "{color.green.700}" },     // #0b5850
      "pressed": { "value": "{color.green.800}" },   // #0a4942
      "muted": { "value": "{color.green.100}" }      // #d8efe9
    }
  }
}
```

### Dark Theme

**Surface Colors**:
```json
{
  "color": {
    "surface": {
      "base": { "value": "{color.neutral.900}" },      // #0c1411
      "raised": { "value": "{color.neutral.800}" },    // #14201c
      "sunken": { "value": "{color.neutral.950}" }     // #070c0a
    }
  }
}
```

**Text Colors**:
```json
{
  "color": {
    "text": {
      "primary": { "value": "{color.neutral.50}" },      // #f7faf8
      "secondary": { "value": "{color.neutral.300}" },   // #b8cdc4
      "disabled": { "value": "{color.neutral.400}" },    // #95b4a7
      "inverse": { "value": "{color.neutral.900}" }      // #0c1411
    }
  }
}
```

**Accent Colors** (lighter in dark mode):
```json
{
  "color": {
    "accent": {
      "default": { "value": "{color.green.400}" },   // #4aa693
      "hover": { "value": "{color.green.300}" },     // #7ac8b5
      "pressed": { "value": "{color.green.200}" },   // #b1ded2
      "muted": { "value": "{color.green.900}" }      // #093d37
    }
  }
}
```

## Style Dictionary Configuration

### Build Configuration

```javascript
import StyleDictionary from "style-dictionary";

// Flatten nested tokens
function flatten(obj, prefix = [], out = {}) {
  for (const [k, v] of Object.entries(obj)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      if ("value" in v) {
        out[[...prefix, k].join("-")] = String(v.value);
      } else {
        flatten(v, [...prefix, k], out);
      }
    }
  }
  return out;
}

// Build theme
async function buildTheme(name, sources) {
  const raw = {};
  for (const src of sources) {
    const data = await import(src, { with: { type: "json" } });
    Object.assign(raw, data.default);
  }

  return flatten(raw);
}

// Generate light theme
const lightFlat = await buildTheme("light", [
  "./src/primitive/base.json",
  "./src/semantic/light.json",
]);

// Generate dark theme
const darkFlat = await buildTheme("dark", [
  "./src/primitive/base.json",
  "./src/semantic/dark.json",
]);

// Write CSS
const css = generateCSS(lightFlat, darkFlat);
await fs.writeFile("./dist/tokens.css", css);
```

### CSS Output Format

```css
/* Generated by tokens — do not edit by hand */

/* Light theme (default) */
:root,
[data-theme="light"] {
  --color-green-50: #eef8f4;
  --color-green-600: #0f6b5c;
  --color-neutral-0: #ffffff;
  --color-neutral-800: #14201c;

  --color-surface-base: #f7faf8;
  --color-surface-raised: #ffffff;
  --color-text-primary: #14201c;
  --color-accent-default: #0f6b5c;

  --space-1: 4px;
  --space-4: 16px;
  --radius-md: 8px;
  --motion-fast: 120ms;
  --font-body: "Source Sans 3", system-ui, sans-serif;
}

/* Dark theme */
[data-theme="dark"] {
  --color-surface-base: #0c1411;
  --color-surface-raised: #14201c;
  --color-text-primary: #f7faf8;
  --color-accent-default: #4aa693;
}

/* Density modes */
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

/* Motion reduction */
@media (prefers-reduced-motion: reduce) {
  :root {
    --motion-fast: 0ms;
    --motion-base: 0ms;
    --motion-slow: 0ms;
  }
}

/* High contrast (accessibility) */
@media (prefers-contrast: more) {
  :root {
    --color-border-subtle: var(--color-neutral-700);
    --color-border-strong: var(--color-neutral-900);
  }
}
```

## Theme System Implementation

### HTML Setup (Anti-FOUC)

```html
<!doctype html>
<html lang="en">
  <head>
    <title>App</title>

    <!-- Apply theme BEFORE first render -->
    <script>
      (function () {
        const stored = localStorage.getItem("theme") || "system";
        const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
        const dark = stored === "dark" || (stored === "system" && prefersDark);

        document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
        document.documentElement.setAttribute("data-density",
          localStorage.getItem("density") || "comfortable"
        );
      })();
    </script>
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
```

### Theme Toggle Component

```typescript
type ThemeMode = "light" | "dark" | "system";

function ThemeToggle() {
  const [mode, setMode] = useState<ThemeMode>(() =>
    (localStorage.getItem("theme") as ThemeMode) || "system"
  );

  useEffect(() => {
    const applyTheme = () => {
      let dark = mode === "dark";
      if (mode === "system") {
        dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      }
      document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
      localStorage.setItem("theme", mode);
    };

    applyTheme();

    // Listen for system preference changes
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    mediaQuery.addEventListener("change", applyTheme);
    return () => mediaQuery.removeEventListener("change", applyTheme);
  }, [mode]);

  return (
    <Menu>
      <MenuItem onClick={() => setMode("light")}>Light</MenuItem>
      <MenuItem onClick={() => setMode("dark")}>Dark</MenuItem>
      <MenuItem onClick={() => setMode("system")}>System</MenuItem>
    </Menu>
  );
}
```

## Using Tokens in Components

### CSS Modules

```css
/* Button.module.css */
.button {
  /* Spacing */
  padding: var(--density-padding-y) var(--space-4);
  gap: var(--density-gap);
  min-height: var(--density-row-height);

  /* Colors */
  background: var(--color-accent-default);
  color: var(--color-text-inverse);

  /* Typography */
  font-family: var(--font-body);
  font-size: var(--text-body);

  /* Borders */
  border-radius: var(--radius-md);

  /* Motion */
  transition: background var(--motion-fast) var(--motion-ease);
}

.button:hover {
  background: var(--color-accent-hover);
}

.button:active {
  background: var(--color-accent-pressed);
}

.button:disabled {
  background: var(--color-surface-sunken);
  color: var(--color-text-disabled);
  cursor: not-allowed;
}
```

### TypeScript Access

```typescript
// Export tokens as TypeScript for programmatic access
export const tokens = {
  color: {
    accent: {
      default: "var(--color-accent-default)",
      hover: "var(--color-accent-hover)",
    },
  },
  space: {
    1: "var(--space-1)",
    4: "var(--space-4)",
  },
} as const;

// Usage
const styles = {
  padding: tokens.space[4],
  color: tokens.color.accent.default,
};
```

## Best Practices

### 1. Always Use Semantic Tokens in Components

```css
/* GOOD: Semantic, theme-aware */
.card {
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
  border: 1px solid var(--color-border-subtle);
}

/* BAD: Hard-coded, not theme-aware */
.card {
  background: #ffffff;
  color: #14201c;
  border: 1px solid #dce7e1;
}
```

### 2. Use Spacing Scale Consistently

```css
/* GOOD: Uses spacing scale */
.container {
  padding: var(--space-4);
  gap: var(--space-2);
  margin-bottom: var(--space-6);
}

/* BAD: Magic numbers */
.container {
  padding: 15px;
  gap: 7px;
  margin-bottom: 23px;
}
```

### 3. Respect Density Mode

```css
/* GOOD: Density-aware */
.row {
  min-height: var(--density-row-height);
  padding: var(--density-padding-y) var(--space-3);
  gap: var(--density-gap);
}

/* BAD: Fixed sizing */
.row {
  height: 44px;
  padding: 12px;
}
```

### 4. Use Motion Tokens

```css
/* GOOD: Motion token */
.button {
  transition: background var(--motion-fast) var(--motion-ease);
}

/* BAD: Hard-coded timing */
.button {
  transition: background 150ms ease-in-out;
}
```

## Extending the Design System

### Adding New Colors

```json
// primitive/base.json
{
  "color": {
    "purple": {
      "100": { "value": "#e6d5f5" },
      "500": { "value": "#7c3aed" },
      "900": { "value": "#3b1c6f" }
    }
  }
}

// semantic/light.json
{
  "color": {
    "brand": {
      "default": { "value": "{color.purple.500}" },
      "muted": { "value": "{color.purple.100}" }
    }
  }
}
```

### Adding New Spacing

```json
{
  "space": {
    "9": { "value": "36px" },
    "10": { "value": "40px" }
  }
}
```

### Custom Tokens

```json
{
  "z-index": {
    "dropdown": { "value": "100" },
    "modal": { "value": "200" },
    "toast": { "value": "300" }
  }
}
```

## Conclusion

A robust token system provides:
- **Consistency** across the application
- **Theme-ability** with minimal effort
- **Maintainability** through centralized values
- **Accessibility** with semantic naming
- **Scalability** for growing design needs

The patterns shown here enable design system evolution without breaking existing components.
