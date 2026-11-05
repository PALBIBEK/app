'use client';

/**
 * One A2UI surface, drawn by Google's React renderer (`@a2ui/react`) on the basic catalog,
 * in the app's colours. Loaded only in the browser (./ReportCard): the catalog's components
 * are Web Components.
 */

import type { CSSProperties } from 'react';
import { A2uiSurface, MarkdownComponentContext } from '@a2ui/react/v0_9';

import type { ReportSurfaceModel } from '@/lib/session/useReports';

import { Markdown } from './Markdown';

/** The basic catalog's theme variables, set to the app's palette (tailwind.config). */
const THEME = {
  '--a2ui-color-background': '#FFFFFF',
  '--a2ui-color-on-background': '#1C1F24',
  '--a2ui-color-surface': '#F6F7F9',
  '--a2ui-color-on-surface': '#1C1F24',
  '--a2ui-color-border': '#E2E5EA',
  '--a2ui-color-secondary': '#ECEEF2',
  '--a2ui-color-on-secondary': '#1C1F24',
  '--a2ui-border-radius': '8px',
  '--a2ui-font-family-monospace': 'var(--font-geist-mono), ui-monospace, monospace',
  colorScheme: 'light',
} as CSSProperties;

export default function ReportSurface({ surface }: { surface: ReportSurfaceModel }): JSX.Element {
  return (
    <div data-testid="report-surface" style={THEME} className="text-[14px] leading-relaxed text-ink">
      <MarkdownComponentContext.Provider value={Markdown}>
        <A2uiSurface surface={surface} />
      </MarkdownComponentContext.Provider>
    </div>
  );
}
