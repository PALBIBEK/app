import { afterEach, describe, expect, it, vi } from 'vitest';

import { downloadPng, PRINT_TARGET, printReport, reportName } from './exportReport';

vi.mock('html-to-image', () => ({ toPng: vi.fn(async () => 'data:image/png;base64,AAAA') }));

afterEach(() => vi.restoreAllMocks());

describe('a report out of the chat', () => {
  it('is named after its surface, safely', () => {
    expect(reportName('prices')).toBe('report-prices');
    expect(reportName('../a b/ c?')).toBe('report-a-b-c');
    expect(reportName('///')).toBe('report-report');
  });

  it('saves as a PNG of what is drawn', async () => {
    const { toPng } = await import('html-to-image');
    const clicked: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this);
    });
    const node = document.createElement('div');

    await downloadPng(node, 'report-prices.png');

    expect(toPng).toHaveBeenCalledWith(node, expect.objectContaining({ pixelRatio: 2 }));
    expect(clicked.map((a) => [a.download, a.href])).toEqual([['report-prices.png', 'data:image/png;base64,AAAA']]);
  });

  it('prints alone, titled for its PDF, and puts the page back after', () => {
    const node = document.createElement('div');
    document.title = 'Session';
    let during: [boolean, boolean, string] | null = null;
    vi.spyOn(window, 'print').mockImplementation(() => {
      during = [node.hasAttribute(PRINT_TARGET), document.documentElement.hasAttribute('data-printing'), document.title];
    });

    printReport(node, 'report-prices');
    window.dispatchEvent(new Event('afterprint'));

    expect(during).toEqual([true, true, 'report-prices']);
    expect([node.hasAttribute(PRINT_TARGET), document.documentElement.hasAttribute('data-printing'), document.title]).toEqual([false, false, 'Session']);
  });
});
