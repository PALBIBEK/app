import { describe, expect, it } from 'vitest';

import { breadcrumb, folderView, joinPath, validName } from './tree';

const files = ['README.md', 'notes/plan.md', 'notes/q4/report.pdf', 'notes/q4/sales.csv', 'out/chart.png'].map((path) => ({ path }));

describe('folderView', () => {
  it('lists the top level: folders with file counts, then files', () => {
    const v = folderView(files, '');
    expect(v.folders).toEqual([
      { name: 'notes', path: 'notes', count: 3 },
      { name: 'out', path: 'out', count: 1 },
    ]);
    expect(v.files.map((f) => f.path)).toEqual(['README.md']);
  });

  it('lists a nested folder, and a folder made in the panel that has no files yet', () => {
    const v = folderView(files, 'notes/', ['notes/drafts', 'elsewhere/x']);
    expect(v.folders.map((f) => [f.name, f.count])).toEqual([['drafts', 0], ['q4', 2]]);
    expect(v.files.map((f) => f.path)).toEqual(['notes/plan.md']);
  });
});

describe('paths', () => {
  it('builds breadcrumbs and joins names', () => {
    expect(breadcrumb('notes/q4').map((c) => c.path)).toEqual(['', 'notes', 'notes/q4']);
    expect(joinPath('', 'a.txt')).toBe('a.txt');
    expect(joinPath('notes/q4/', 'a.txt')).toBe('notes/q4/a.txt');
  });

  it('accepts one plain name only', () => {
    expect(validName(' Q4 Research ')).toBe('Q4 Research');
    for (const bad of ['', '..', 'a/b', 'a\\b']) expect(validName(bad)).toBeNull();
  });
});
