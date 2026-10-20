# Frontend Build Configuration
## Auto-Applied Setup for All Frontend Development

**Purpose**: This file is automatically referenced when building any frontend component
**Auto-Apply**: YES - Use these rules for EVERY frontend request

---

## Stack Configuration

```yaml
framework: Next.js 14+ (App Router)
language: TypeScript
styling: Tailwind CSS + CSS Modules (when needed)
components: Radix UI + shadcn/ui
icons: Phosphor Icons (NO emoji)
animation: Framer Motion
state: Zustand (lightweight) or React Context
forms: React Hook Form + Zod validation
testing: Playwright (E2E) + Vitest (unit)
```

---

## Mandatory Design Rules (Taste Skill)

### ❌ NEVER USE:
- Purple gradients or purple as primary color
- Emoji anywhere (use Phosphor icons instead)
- Centered hero sections (use asymmetric layouts)
- Default Inter font without customization
- Cards nested inside cards
- `h-screen` (use `min-h-[100dvh]` instead)
- Flexbox math (use CSS Grid)
- Generic "professional" stock photos
- Lorem ipsum (use realistic placeholder content)
- Em dashes (—) anywhere in UI text

### ✅ ALWAYS USE:
- Asymmetric layouts (content aligned left/right, not center)
- Phosphor icons exclusively
- CSS Grid for layouts (not flexbox)
- `min-h-[100dvh]` for full-height sections
- 4px base spacing unit (4, 8, 12, 16, 24, 32, 48)
- Realistic placeholder content
- Subtle animations (no floating cards)
- Border accents instead of shadows for emphasis
- Monospace fonts for code/data

---

## Brand Design System

### Colors (NO purple!)
```typescript
// Primary: Blue
primary: {
  50: '#eff6ff',
  100: '#dbeafe',
  500: '#3b82f6',  // Main
  600: '#2563eb',  // Hover
  900: '#1e3a8a',
}

// Secondary: Green
secondary: {
  50: '#f0fdf4',
  500: '#10b981',
  600: '#059669',
}

// Accent: Amber
accent: {
  50: '#fffbeb',
  500: '#f59e0b',
  600: '#d97706',
}

// Neutrals: Gray
gray: {
  50: '#f9fafb',
  100: '#f3f4f6',
  500: '#6b7280',
  900: '#111827',
}
```

### Typography
```typescript
fontFamily: {
  sans: ['Inter', 'system-ui', 'sans-serif'],
  mono: ['JetBrains Mono', 'Consolas', 'monospace'],
}

fontSize: {
  xs: '0.75rem',    // 12px
  sm: '0.875rem',   // 14px
  base: '1rem',     // 16px
  lg: '1.125rem',   // 18px
  xl: '1.25rem',    // 20px
  '2xl': '1.5rem',  // 24px
  '3xl': '1.875rem', // 30px
  '4xl': '2.25rem',  // 36px
}
```

### Spacing (4px base unit)
```typescript
spacing: {
  '1': '0.25rem',  // 4px
  '2': '0.5rem',   // 8px
  '3': '0.75rem',  // 12px
  '4': '1rem',     // 16px
  '6': '1.5rem',   // 24px
  '8': '2rem',     // 32px
  '12': '3rem',    // 48px
  '16': '4rem',    // 64px
}
```

### Border Radius
```typescript
borderRadius: {
  sm: '0.25rem',   // 4px
  md: '0.5rem',    // 8px
  lg: '0.75rem',   // 12px
  xl: '1rem',      // 16px
}
```

---

## Component Patterns

### File Structure
```
components/
├── ui/                    # Design system primitives
│   ├── button.tsx
│   ├── card.tsx
│   ├── dialog.tsx
│   └── input.tsx
├── agent/                 # Domain components
│   ├── AgentCard.tsx
│   ├── ConversationView.tsx
│   └── ToolExecutionCard.tsx
└── layout/
    ├── Header.tsx
    └── Sidebar.tsx
```

### Component Template
```typescript
/**
 * ComponentName
 *
 * Design: [Asymmetric/Grid/etc] layout, [Low/Medium/High] motion
 * Icons: Phosphor
 *
 * Anti-patterns avoided:
 * - [List specific anti-patterns avoided]
 */

import { Icon } from '@phosphor-icons/react';
import { ComponentProps } from '@/types';

interface ComponentNameProps {
  // Props with JSDoc
}

export function ComponentName({ prop1, prop2 }: ComponentNameProps) {
  return (
    <div className="grid grid-cols-12 gap-6 p-6">
      {/* Grid layout, not flexbox */}
      {/* Asymmetric content alignment */}
      {/* Phosphor icons only */}
    </div>
  );
}
```

---

## Layout Rules

### Grid System (12 columns)
```tsx
// Desktop
<div className="grid grid-cols-12 gap-6">
  <div className="col-span-8">Main content</div>
  <div className="col-span-4">Sidebar</div>
</div>

// Responsive
<div className="grid grid-cols-1 md:grid-cols-12 gap-6">
  <div className="md:col-span-8">Main</div>
  <div className="md:col-span-4">Sidebar</div>
</div>
```

### Asymmetric Layouts
```tsx
// ✅ Good: Content aligned left, visual interest on right
<section className="grid grid-cols-12 gap-8">
  <div className="col-span-7">
    <h1>Title</h1>
    <p>Content</p>
  </div>
  <div className="col-span-5">
    <Image />
  </div>
</section>

// ❌ Bad: Centered hero
<section className="flex flex-col items-center">
  <h1>Title</h1>
</section>
```

---

## Animation Guidelines

### Motion Intensity: Medium
```tsx
// ✅ Subtle hover transitions
className="transition-all duration-200 hover:shadow-lg"

// ✅ Entrance animations
<motion.div
  initial={{ opacity: 0, y: 10 }}
  animate={{ opacity: 1, y: 0 }}
  transition={{ duration: 0.2 }}
>

// ❌ No floating cards
// ❌ No excessive bouncing
// ❌ No auto-playing animations
```

---

## Accessibility Requirements

### Mandatory Checks
- [ ] All interactive elements keyboard navigable (Tab, Enter, Space)
- [ ] ARIA labels on icon-only buttons
- [ ] Color contrast ratio ≥ 4.5:1 (text), ≥ 3:1 (UI components)
- [ ] Focus indicators clearly visible
- [ ] Support `prefers-reduced-motion`
- [ ] Semantic HTML (button, nav, main, article, etc.)
- [ ] Alt text on all images

### Code Pattern
```tsx
// ✅ Accessible button
<button
  aria-label="Close dialog"
  className="focus:ring-2 focus:ring-primary focus:outline-none"
>
  <X size={20} />
</button>

// ❌ Non-accessible
<div onClick={handleClick}>
  <X size={20} />
</div>
```

---

## Testing Requirements

### Every Component Must Have:

1. **Visual Test** (Playwright screenshot)
```typescript
test('ComponentName visual regression', async ({ page }) => {
  await page.goto('/component-showcase');
  await page.screenshot({
    path: 'tests/visual/component-name.png',
    fullPage: true
  });
});
```

2. **Interaction Test**
```typescript
test('ComponentName interactions', async ({ page }) => {
  await page.click('[data-testid="component-trigger"]');
  await expect(page.locator('[data-testid="component-result"]')).toBeVisible();
});
```

3. **Accessibility Test**
```typescript
test('ComponentName accessibility', async ({ page }) => {
  // Keyboard navigation
  await page.press('body', 'Tab');
  await expect(page.locator('[data-testid="first-focusable"]')).toBeFocused();
});
```

---

## Code Quality Standards

### TypeScript
```typescript
// ✅ Strict mode enabled
// ✅ No 'any' types (use 'unknown' if needed)
// ✅ Props interfaces exported
// ✅ Return types on functions

interface ButtonProps {
  variant: 'primary' | 'secondary' | 'ghost';
  size?: 'sm' | 'md' | 'lg';
  children: React.ReactNode;
  onClick?: () => void;
}

export function Button({
  variant,
  size = 'md',
  children,
  onClick
}: ButtonProps): JSX.Element {
  // Implementation
}
```

### Tailwind
```tsx
// ✅ Use cn() utility for conditional classes
import { cn } from '@/lib/utils';

<button className={cn(
  'px-4 py-2 rounded-lg transition-colors',
  variant === 'primary' && 'bg-primary text-white',
  variant === 'secondary' && 'bg-gray-100 text-gray-900',
)}>

// ❌ Don't use inline conditionals
<button className={variant === 'primary' ? 'bg-blue-500' : 'bg-gray-100'}>
```

---

## Response Format

When you ask me to build a component, I will provide:

### 1. Component Code
```typescript
// Full implementation with:
// - TypeScript types
// - Tailwind classes
// - Phosphor icons
// - Accessibility attributes
// - Comments explaining design decisions
```

### 2. Design Justification
```markdown
## Design Decisions

✅ **Followed Rules:**
- Asymmetric layout (content left-aligned)
- Phosphor icons (User, MessageCircle)
- Grid layout (not flexbox)
- 4px spacing units
- No purple gradients

❌ **Avoided Anti-Patterns:**
- No centered hero
- No emoji
- No card nesting
- No purple
```

### 3. Usage Example
```tsx
// How to use the component
import { ComponentName } from '@/components/agent/ComponentName';

<ComponentName
  prop1="value"
  prop2={data}
/>
```

### 4. Playwright Test
```typescript
// E2E test for the component
test('ComponentName flow', async ({ page }) => {
  // Test implementation
});
```

---

## Quick Reference Checklist

Before delivering any component, verify:

- [ ] Uses Phosphor icons (NO emoji)
- [ ] Asymmetric layout (NOT centered)
- [ ] CSS Grid for structure (NOT flexbox math)
- [ ] `min-h-[100dvh]` (NOT `h-screen`)
- [ ] 4px spacing units (4, 8, 12, 16, 24, 32, 48)
- [ ] NO purple gradients
- [ ] NO em dashes in text
- [ ] ARIA labels on icon buttons
- [ ] Focus indicators visible
- [ ] Color contrast ≥ 4.5:1
- [ ] TypeScript types defined
- [ ] Playwright test included
- [ ] Realistic placeholder content (NO lorem ipsum)

---

## Auto-Applied Stack

```json
{
  "framework": "Next.js 14 (App Router)",
  "language": "TypeScript (strict mode)",
  "styling": "Tailwind CSS",
  "components": "Radix UI + shadcn/ui",
  "icons": "Phosphor Icons",
  "animation": "Framer Motion",
  "forms": "React Hook Form + Zod",
  "state": "Zustand or React Context",
  "testing": "Playwright + Vitest",
  "linting": "ESLint + Prettier",
  "formatting": "Prettier (4 spaces)"
}
```

---

## File Locations

```
frontend/
├── src/
│   ├── components/
│   │   ├── ui/           # Radix + shadcn primitives
│   │   ├── agent/        # Agent-specific components
│   │   └── layout/       # Layout components
│   ├── lib/
│   │   ├── utils.ts      # cn() and utilities
│   │   └── tokens.ts     # Design tokens
│   ├── styles/
│   │   └── globals.css   # Tailwind imports + custom CSS
│   ├── types/
│   │   └── index.ts      # Shared TypeScript types
│   └── app/              # Next.js app router
├── tests/
│   ├── e2e/              # Playwright E2E tests
│   └── visual/           # Visual regression screenshots
├── SKILL.md              # Taste Skill rules
├── DESIGN.md             # Custom brand rules
├── tailwind.config.ts
├── playwright.config.ts
└── package.json
```

---

## Important Notes

1. **This config is ALWAYS applied** - You don't need to remind me
2. **I will reference this file** for every frontend build request
3. **Deviations require explicit approval** - If you want something different, you'll tell me
4. **I will justify design decisions** - Every choice traced back to these rules

---

**READY TO BUILD** - Just tell me what component you need!
