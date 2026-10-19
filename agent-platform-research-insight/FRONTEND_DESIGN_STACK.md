# Modern Frontend Design Stack for Agentic Platforms
## Using Taste Skill + Impeccable + Playwright CLI + Awesome Design Systems

**Created**: 2026-08-28
**Purpose**: Guide for building production-quality frontend for prime-agent-study platform

---

## Table of Contents

1. [Overview](#overview)
2. [The Tools](#the-tools)
3. [Design Philosophy](#design-philosophy)
4. [Installation & Setup](#installation--setup)
5. [Integration Guide](#integration-guide)
6. [Workflow & Best Practices](#workflow--best-practices)
7. [Practical Examples](#practical-examples)
8. [Design System Selection](#design-system-selection)

---

## Overview

This stack combines four powerful tools to build a production-ready, AI-assisted frontend that doesn't look "AI-generated":

| Tool | Purpose | Key Benefit |
|------|---------|-------------|
| **Taste Skill** | Anti-slop design rules | Prevents generic AI aesthetics |
| **Impeccable** | Design vocabulary & guardrails | Enforces brand consistency |
| **Playwright CLI** | Browser automation & testing | End-to-end testing + visual QA |
| **Awesome Design Systems** | Component libraries | Production-ready UI components |

---

## The Tools

### 1. Taste Skill - The Anti-Slop Frontend Framework

**What it is**: An open-source skill file that stops AI coding agents (Cursor, Claude Code, Codex) from generating generic, boring frontends.

**Key Features**:
- Reads design briefs to infer aesthetic direction (SaaS minimalist, agency experimental, premium consumer)
- Configures three core dials: design variance, motion intensity, visual density
- Enforces strict anti-pattern rules (no purple gradients, no emoji spam, no centered heroes)
- Built for React/Next.js with Tailwind
- Mandates Phosphor icons over emojis
- Grid over flexbox math
- `min-h-[100dvh]` instead of `h-screen` (prevents mobile viewport collapse)

**Official Sites**:
- [Taste Skill Documentation](https://www.tasteskill.dev/docs)
- [Taste Skill Homepage](https://www.tasteskill.dev/)
- [Skills Directory](https://skillselion.com/skills/leonxlnx/taste-skill/design-taste-frontend)

**Install Count**: 403,029+ installations (as of Aug 2026)

**Best For**: Landing pages, portfolios, redesigns
**Not For**: Dashboards, data tables, multi-step product UIs

---

### 2. Impeccable - Design-to-Code Framework

**What it is**: An AI design skill and CLI workflow by Paul Bakaus that helps coding agents make better product design decisions.

**Key Features**:
- Respects existing design systems (scans tokens, components, Tailwind config)
- Loads brand rules from your own `DESIGN.md`
- 23 commands for shared design vocabulary
- 58 deterministic anti-pattern detection rules
- Runs inside Claude Code, Cursor, Codex, GitHub Copilot, Gemini CLI

**Commands**:
- `polish` - Refine UI with design system rules
- `critique` - Get design feedback
- `audit` - Check for anti-patterns
- `live` - Real-time design enforcement

**Official Resource**:
- [Impeccable Design-to-Code Blog Post](https://designproject.io/blog/impeccable-design-to-code/)
- [Mastering Impeccable](https://dev.to/turingsoracle/mastering-impeccable-ai-skill-design-for-frontend-architecture-lbe)

**Best For**: Maintaining design consistency across AI-generated code

---

### 3. Playwright CLI - Browser Automation

**What it is**: Token-efficient command-line interface for browser automation designed for coding agents.

**Key Features**:
- Headless browser automation by default (`--headed` to show browser)
- Token-efficient (avoids loading large tool schemas)
- YAML flow recording for test generation
- Screenshot capabilities
- Console log inspection

**Common Commands**:
```bash
playwright-cli open [url]              # Open browser
playwright-cli goto <url>              # Navigate
playwright-cli click <ref>             # Click element
playwright-cli fill <ref> <text>       # Fill form
playwright-cli type <text>             # Type text
playwright-cli screenshot <path>       # Take screenshot
```

**Official Docs**:
- [Playwright CLI Installation](https://playwright.dev/agent-cli/installation)
- [Playwright CLI Configuration](https://playwright.dev/agent-cli/configuration)
- [Getting Started with CLI](https://playwright.dev/docs/getting-started-cli)

**Use Cases for Frontend Development**:
1. **Generating E2E tests**: Record user flows, generate test code
2. **Debugging UI issues**: Inspect elements, take screenshots, diagnose problems
3. **Exploratory testing**: Automated UI exploration
4. **Visual regression testing**: Compare screenshots across builds

---

### 4. Awesome Design Systems

**What it is**: Curated collections of production-ready design systems and component libraries.

**Top Open Source Options** (2024):

| Design System | Company | GitHub Stars | Best For |
|---------------|---------|--------------|----------|
| **Material Design** | Google | Industry standard | Cross-platform consistency |
| **Blueprint** | Palantir | 20.3k+ | Enterprise apps, complex data |
| **Polaris** | Shopify | High | E-commerce, merchant tools |
| **Gestalt** | Pinterest | Medium | Visual-first applications |
| **Chakra UI** | Community | 37k+ | Fast prototyping, accessibility |
| **Radix UI** | Community | 15k+ | Unstyled primitives, full control |

**Official Resources**:
- [Awesome Design Systems GitHub](https://github.com/klaufel/awesome-design-systems)
- [GreatFrontEnd's Top Design Systems](https://www.greatfrontend.com/blog/top-open-source-design-systems-by-tech-companies-you-can-use-right-away)
- [Open Awesome Directory](https://open-awesome.com/projects/awesome-design-systems)

---

## Design Philosophy

### The Anti-Slop Approach

**Problem**: AI-generated UIs often look generic:
- Purple-to-blue gradients everywhere
- Centered heroes with floating cards
- Emoji spam in headings
- Default Inter font
- Cards nested in cards
- Generic "professional" aesthetic

**Solution**: Enforce strict design rules at the AI level

### Three-Layer Strategy

```
Layer 1: Design System (Radix/Chakra/Blueprint)
   ↓ Provides: Base components, accessibility, tokens

Layer 2: Taste Skill + Impeccable
   ↓ Provides: Aesthetic guardrails, anti-patterns, brand rules

Layer 3: Playwright CLI
   ↓ Provides: Visual QA, E2E testing, regression checks
```

---

## Installation & Setup

### 1. Install Taste Skill

**For Claude Code:**
```bash
# Navigate to your project
cd /path/to/prime-agent-study-main

# Create SKILL.md file
curl -o SKILL.md https://raw.githubusercontent.com/leonxlnx/taste-skill/main/SKILL.md

# Or manually create SKILL.md with Taste Skill content
```

**For Cursor:**
```bash
# Add to .cursorrules file
echo "source: https://www.tasteskill.dev/docs" >> .cursorrules
```

**Verify Installation**:
Ask your AI assistant: "Use Taste Skill to design a landing page for an AI agent platform"

---

### 2. Install Impeccable

**Step 1: Install as Skill**
```bash
# Download Impeccable skill file
curl -o DESIGN.md https://raw.githubusercontent.com/pbakaus/impeccable/main/DESIGN.md

# Or create from template (see below)
```

**Step 2: Create Your Brand DESIGN.md**
```markdown
# Design System

## Brand Identity
- **Primary Color**: #2563EB (Blue 600)
- **Secondary Color**: #10B981 (Green 500)
- **Accent Color**: #F59E0B (Amber 500)
- **Typography**: Inter for UI, JetBrains Mono for code
- **Tone**: Professional, trustworthy, innovative

## Anti-Patterns to Avoid
- No purple gradients
- No emoji in headings
- No centered hero sections (use asymmetric layouts)
- No default shadows (use custom elevation system)
- No "card in card" nesting

## Component Standards
- Buttons: Solid primary, outline secondary, ghost tertiary
- Spacing: 4px base unit (use 4, 8, 12, 16, 24, 32, 48)
- Radius: sm=4px, md=8px, lg=12px, xl=16px
- Max width for prose: 65ch
- Icons: Phosphor icon family only
```

---

### 3. Install Playwright CLI

```bash
# Install globally
npm install -g @playwright/cli

# Or in project
npm install --save-dev @playwright/cli

# Install browser (Chromium by default)
playwright-cli install-browser

# For Firefox
playwright-cli install-browser firefox
```

**Verify**:
```bash
playwright-cli open https://example.com --headed
```

---

### 4. Choose & Install Design System

**For Agentic Platform (Recommended: Radix UI + Tailwind)**

```bash
# Install Radix UI primitives
npm install @radix-ui/react-dialog @radix-ui/react-dropdown-menu @radix-ui/react-tabs @radix-ui/react-tooltip

# Install Tailwind CSS
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p

# Install shadcn/ui (built on Radix)
npx shadcn-ui@latest init
```

**Why Radix?**
- Unstyled primitives (full design control)
- Excellent accessibility (ARIA compliant)
- Works perfectly with Tailwind
- No design opinions (Taste Skill provides those)

**Alternative: Chakra UI (faster setup)**
```bash
npm install @chakra-ui/react @emotion/react @emotion/styled framer-motion
```

---

## Integration Guide

### Project Structure

```
prime-agent-study-main/
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── ui/              # Design system components
│   │   │   │   ├── button.tsx
│   │   │   │   ├── dialog.tsx
│   │   │   │   └── card.tsx
│   │   │   ├── agent/           # Agent-specific components
│   │   │   │   ├── AgentCard.tsx
│   │   │   │   ├── ToolRegistry.tsx
│   │   │   │   └── ConversationView.tsx
│   │   │   └── layout/
│   │   │       ├── Header.tsx
│   │   │       └── Sidebar.tsx
│   │   ├── pages/
│   │   ├── styles/
│   │   │   └── globals.css
│   │   └── lib/
│   │       └── utils.ts
│   ├── tests/
│   │   ├── e2e/                 # Playwright tests
│   │   └── visual/              # Visual regression
│   ├── SKILL.md                 # Taste Skill rules
│   ├── DESIGN.md                # Impeccable + brand rules
│   ├── tailwind.config.js
│   ├── playwright.config.ts
│   └── package.json
└── backend/
```

---

### Configuration Files

**tailwind.config.js** (with Taste Skill defaults):
```javascript
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // No purple! Custom brand colors
        primary: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          // ... blue scale
          900: '#1e3a8a',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      spacing: {
        // 4px base unit
        '4': '1rem',
        '8': '2rem',
        '12': '3rem',
        '16': '4rem',
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
    require('@tailwindcss/typography'),
  ],
}
```

**playwright.config.ts**:
```typescript
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',

  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],

  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
});
```

---

## Workflow & Best Practices

### Development Workflow

**1. Design Brief → AI Generation**

```bash
# Step 1: Write design brief
cat > design-brief.md <<EOF
# Agent Dashboard Design Brief

## Aesthetic Direction
- **Style**: Premium consumer (think Apple, Linear)
- **Variance**: Low (consistent, predictable)
- **Motion**: Medium (subtle animations, no floating)
- **Density**: Medium (breathing room, not sparse)

## User Journey
User lands on dashboard → sees active agents → can start new conversation

## Key Interactions
- Quick agent selection
- Real-time conversation stream
- Tool execution visualization
EOF

# Step 2: Generate with AI assistant
# "Using Taste Skill and the design brief, create an agent dashboard component"
```

**2. AI Generates Code with Guardrails**

The AI will:
- Read `SKILL.md` (Taste Skill rules)
- Read `DESIGN.md` (Impeccable brand rules)
- Read `design-brief.md` (your specific requirements)
- Generate code that follows all constraints

**3. Impeccable Review**

```bash
# Run design audit
impeccable audit src/components/AgentDashboard.tsx

# Get specific feedback
impeccable critique src/components/AgentDashboard.tsx --focus=spacing

# Polish a component
impeccable polish src/components/AgentDashboard.tsx
```

**4. Visual Testing with Playwright**

```bash
# Generate test from user flow
playwright-cli open http://localhost:3000 --headed

# AI watches you interact, generates test:
# "Record my actions as a Playwright test"

# Run E2E tests
npm run test:e2e

# Visual regression
npm run test:visual
```

---

### Best Practices

**1. Design Token Hierarchy**

```typescript
// src/lib/tokens.ts
export const tokens = {
  spacing: {
    xs: '0.25rem',  // 4px
    sm: '0.5rem',   // 8px
    md: '0.75rem',  // 12px
    lg: '1rem',     // 16px
    xl: '1.5rem',   // 24px
  },
  colors: {
    primary: {
      base: '#2563EB',
      hover: '#1D4ED8',
      active: '#1E40AF',
    },
    // ...
  },
  typography: {
    sizes: {
      xs: '0.75rem',
      sm: '0.875rem',
      base: '1rem',
      lg: '1.125rem',
      xl: '1.25rem',
    },
  },
} as const;
```

**2. Component Documentation**

```typescript
/**
 * AgentCard Component
 *
 * Design System: Radix UI + Tailwind
 * Aesthetic: Premium consumer (low variance, medium motion)
 *
 * Anti-patterns avoided:
 * - No centered content (asymmetric layout)
 * - No purple gradients (uses primary brand blue)
 * - No emoji (uses Phosphor icons)
 *
 * @example
 * <AgentCard
 *   name="Customer Support Agent"
 *   status="active"
 *   conversations={12}
 * />
 */
export function AgentCard({ name, status, conversations }: AgentCardProps) {
  // ...
}
```

**3. Accessibility Checklist**

- [ ] All interactive elements keyboard navigable
- [ ] ARIA labels on icon-only buttons
- [ ] Color contrast ratio ≥ 4.5:1
- [ ] Focus indicators visible
- [ ] Screen reader tested
- [ ] No motion for `prefers-reduced-motion`

---

## Practical Examples

### Example 1: Agent Card Component

**Prompt to AI**:
```
Using Taste Skill and our DESIGN.md, create an AgentCard component for the dashboard.

Requirements:
- Shows agent name, status (active/idle), active conversations count
- Click to open conversation view
- Asymmetric layout (not centered)
- Uses Phosphor icons
- Subtle hover animation (medium motion)
```

**AI Generates** (`components/agent/AgentCard.tsx`):
```typescript
import { Activity, MessageCircle } from '@phosphor-icons/react';
import { Card } from '@/components/ui/card';

interface AgentCardProps {
  name: string;
  status: 'active' | 'idle';
  conversations: number;
  onClick: () => void;
}

export function AgentCard({ name, status, conversations, onClick }: AgentCardProps) {
  return (
    <Card
      onClick={onClick}
      className="group relative p-6 cursor-pointer transition-all duration-200 hover:shadow-lg border-l-4 border-primary min-h-[120px]"
    >
      {/* Asymmetric layout - content aligned left */}
      <div className="flex flex-col gap-4">
        {/* Header */}
        <div className="flex items-start justify-between">
          <h3 className="text-lg font-semibold text-gray-900 group-hover:text-primary transition-colors">
            {name}
          </h3>
          <div className={`flex items-center gap-2 px-3 py-1 rounded-full ${
            status === 'active'
              ? 'bg-green-50 text-green-700'
              : 'bg-gray-100 text-gray-600'
          }`}>
            <Activity size={16} weight={status === 'active' ? 'fill' : 'regular'} />
            <span className="text-sm font-medium capitalize">{status}</span>
          </div>
        </div>

        {/* Stats - grid layout, not flexbox math */}
        <div className="grid grid-cols-2 gap-4">
          <div className="flex items-center gap-2 text-gray-600">
            <MessageCircle size={20} />
            <span className="text-sm">
              {conversations} active
            </span>
          </div>
        </div>
      </div>

      {/* Subtle motion: slide indicator on hover */}
      <div className="absolute bottom-0 left-0 h-1 bg-primary w-0 group-hover:w-full transition-all duration-300" />
    </Card>
  );
}
```

**Why This Follows Taste Skill**:
- ✅ Asymmetric layout (not centered)
- ✅ Phosphor icons (not emoji)
- ✅ Grid layout (not flexbox math)
- ✅ Medium motion (subtle slide, not floating)
- ✅ Border-left accent (not centered card)
- ✅ No purple gradients (uses primary blue)

---

### Example 2: Conversation View

**Prompt**:
```
Create a ConversationView component showing agent-user chat.

Design specs (from brief):
- Asymmetric bubble layout
- User messages: right-aligned, primary color
- Agent messages: left-aligned, gray background
- Tool executions: inline cards with phosphor icon
- No emoji, use icons for actions
- Subtle animation for new messages
```

**AI Generates** (`components/agent/ConversationView.tsx`):
```typescript
import { User, Robot, Wrench } from '@phosphor-icons/react';
import { motion } from 'framer-motion';

interface Message {
  id: string;
  role: 'user' | 'agent' | 'tool';
  content: string;
  timestamp: Date;
  toolName?: string;
}

export function ConversationView({ messages }: { messages: Message[] }) {
  return (
    <div className="flex flex-col gap-4 p-6 min-h-[600px]">
      {messages.map((msg) => (
        <motion.div
          key={msg.id}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
          className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}
        >
          {/* Avatar */}
          <div className={`flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center ${
            msg.role === 'user'
              ? 'bg-primary text-white'
              : msg.role === 'agent'
              ? 'bg-gray-200 text-gray-700'
              : 'bg-amber-100 text-amber-700'
          }`}>
            {msg.role === 'user' && <User size={20} weight="fill" />}
            {msg.role === 'agent' && <Robot size={20} weight="fill" />}
            {msg.role === 'tool' && <Wrench size={20} weight="fill" />}
          </div>

          {/* Message Bubble */}
          <div className={`max-w-[70%] ${
            msg.role === 'user'
              ? 'bg-primary text-white'
              : msg.role === 'agent'
              ? 'bg-gray-100 text-gray-900'
              : 'bg-amber-50 text-gray-900 border-2 border-amber-200'
          } rounded-2xl px-4 py-3`}>
            {msg.toolName && (
              <div className="text-xs font-medium mb-1 opacity-75">
                Tool: {msg.toolName}
              </div>
            )}
            <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
            <span className="text-xs opacity-60 mt-2 block">
              {msg.timestamp.toLocaleTimeString()}
            </span>
          </div>
        </motion.div>
      ))}
    </div>
  );
}
```

---

### Example 3: E2E Test with Playwright

**Generate Test with AI**:
```bash
# 1. Open browser in headed mode
playwright-cli open http://localhost:3000 --headed

# 2. Perform user flow manually:
#    - Click "New Conversation"
#    - Select agent
#    - Send message
#    - Wait for response

# 3. Ask AI: "Generate a Playwright test for the flow I just performed"
```

**AI Generates** (`tests/e2e/conversation.spec.ts`):
```typescript
import { test, expect } from '@playwright/test';

test.describe('Agent Conversation', () => {
  test('should start conversation and receive response', async ({ page }) => {
    // Navigate to dashboard
    await page.goto('/');

    // Click new conversation button
    await page.click('button:has-text("New Conversation")');

    // Select agent from dropdown
    await page.click('[data-testid="agent-selector"]');
    await page.click('text=Customer Support Agent');

    // Type message
    await page.fill('[data-testid="message-input"]', 'Hello, I need help');
    await page.press('[data-testid="message-input"]', 'Enter');

    // Wait for agent response
    await expect(page.locator('[data-role="agent"]').first()).toBeVisible({
      timeout: 5000
    });

    // Verify response contains text
    const agentMessage = page.locator('[data-role="agent"]').first();
    await expect(agentMessage).toContainText('How can I assist you');

    // Take screenshot for visual regression
    await page.screenshot({ path: 'tests/visual/conversation-started.png' });
  });

  test('should show tool execution', async ({ page }) => {
    await page.goto('/conversation/test-123');

    // Trigger tool execution
    await page.fill('[data-testid="message-input"]', 'Search for product XYZ');
    await page.press('[data-testid="message-input"]', 'Enter');

    // Wait for tool execution card
    await expect(page.locator('[data-role="tool"]')).toBeVisible();

    // Verify tool name is displayed
    await expect(page.locator('[data-role="tool"]')).toContainText('Tool:');
  });
});
```

---

## Design System Selection

### Decision Matrix

| System | Complexity | Customization | Accessibility | AI-Friendly | Best For |
|--------|-----------|---------------|---------------|-------------|----------|
| **Radix UI** | Medium | Full | Excellent | ⭐⭐⭐⭐⭐ | Custom designs |
| **Chakra UI** | Low | Good | Excellent | ⭐⭐⭐⭐ | Fast prototyping |
| **Blueprint** | High | Limited | Good | ⭐⭐⭐ | Enterprise data apps |
| **Material UI** | Medium | Medium | Excellent | ⭐⭐⭐ | Standard look |
| **Ant Design** | Medium | Medium | Good | ⭐⭐⭐ | Admin panels |

### Recommendation: Radix UI + shadcn/ui

**Why**:
1. **Unstyled primitives** - Full design control (Taste Skill can style freely)
2. **Accessibility built-in** - ARIA compliant out of the box
3. **Works with Tailwind** - Perfect for utility-first approach
4. **shadcn/ui** - Pre-styled components that respect Taste Skill rules
5. **Copy-paste components** - Easy to customize, AI can modify

**Installation**:
```bash
# Initialize shadcn/ui
npx shadcn-ui@latest init

# Add components as needed
npx shadcn-ui@latest add button
npx shadcn-ui@latest add dialog
npx shadcn-ui@latest add card
```

---

## Implementation Roadmap

### Phase 1: Foundation (Week 1)

- [x] Install Taste Skill and Impeccable
- [ ] Set up Next.js + Tailwind + Radix
- [ ] Create `DESIGN.md` with brand rules
- [ ] Configure Playwright
- [ ] Create design token system

### Phase 2: Core Components (Week 2-3)

- [ ] Build UI primitives (Button, Card, Input, Dialog)
- [ ] Create agent components (AgentCard, ConversationView, ToolExecutionCard)
- [ ] Write component documentation
- [ ] Generate E2E tests with Playwright

### Phase 3: Pages & Integration (Week 4)

- [ ] Dashboard page
- [ ] Conversation page
- [ ] Settings page
- [ ] Integrate with backend API
- [ ] Visual regression testing

### Phase 4: Polish & QA (Week 5)

- [ ] Run Impeccable audit on all components
- [ ] Fix accessibility issues
- [ ] Performance optimization
- [ ] Mobile responsive design
- [ ] Final E2E test suite

---

## Resources

### Official Documentation
- [Taste Skill Docs](https://www.tasteskill.dev/docs)
- [Impeccable Blog Post](https://designproject.io/blog/impeccable-design-to-code/)
- [Playwright CLI](https://playwright.dev/agent-cli/installation)
- [Awesome Design Systems](https://github.com/klaufel/awesome-design-systems)
- [Radix UI](https://www.radix-ui.com/)
- [shadcn/ui](https://ui.shadcn.com/)

### Additional Reading
- [Mastering Impeccable](https://dev.to/turingsoracle/mastering-impeccable-ai-skill-design-for-frontend-architecture-lbe)
- [Playwright for Frontend Devs](https://medium.com/@abhishek070189/playwright-mcp-the-frontend-developers-secret-weapon-in-2026-c4fd3f4b6ede)
- [GreatFrontEnd Design Systems](https://www.greatfrontend.com/blog/top-open-source-design-systems-by-tech-companies-you-can-use-right-away)

---

## Next Steps

1. **Initialize Frontend Project**
   ```bash
   cd prime-agent-study-main
   npx create-next-app@latest frontend --typescript --tailwind --app
   ```

2. **Install All Tools**
   ```bash
   # Install design system
   cd frontend
   npx shadcn-ui@latest init

   # Install Playwright
   npm install -D @playwright/test
   npx playwright install

   # Download Taste Skill
   curl -o SKILL.md https://www.tasteskill.dev/SKILL.md
   ```

3. **Configure AI Assistant**
   - Add `SKILL.md` to project root
   - Create `DESIGN.md` with brand rules
   - Tell AI: "Use Taste Skill and DESIGN.md for all frontend work"

4. **Start Building**
   - Begin with design brief for each page
   - Let AI generate with guardrails
   - Review with Impeccable
   - Test with Playwright

---

**Ready to build a production-quality agentic platform frontend that doesn't look AI-generated!**
