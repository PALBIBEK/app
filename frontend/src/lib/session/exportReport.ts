/**
 * A report, out of the chat: as a PNG (drawn from the page with `html-to-image`) or as a PDF
 * (the browser's own print to PDF, of that report alone, so its text stays text). Both take
 * the report as it is drawn now, in its latest state.
 */

/** A report's name for its files: its surface id, made safe. */
export function reportName(surfaceId: string): string {
  const base = surfaceId.replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'report';
  return `report-${base}`;
}

/** Save `node` as a PNG named `fileName`. */
export async function downloadPng(node: HTMLElement, fileName: string): Promise<void> {
  const { toPng } = await import('html-to-image');
  const url = await toPng(node, { pixelRatio: 2, backgroundColor: '#FFFFFF', cacheBust: true });
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.click();
}

/** The attribute that marks the one report a print shows (globals.css, `@media print`). */
export const PRINT_TARGET = 'data-print-target';

/** Print `node` alone (the print dialog saves it as a PDF), titled `title` (the PDF's name). */
export function printReport(node: HTMLElement, title: string): void {
  const before = document.title;
  node.setAttribute(PRINT_TARGET, '');
  document.documentElement.setAttribute('data-printing', '');
  document.title = title; // the PDF's default file name
  const done = (): void => {
    node.removeAttribute(PRINT_TARGET);
    document.documentElement.removeAttribute('data-printing');
    document.title = before;
    window.removeEventListener('afterprint', done);
  };
  window.addEventListener('afterprint', done);
  window.print();
}
