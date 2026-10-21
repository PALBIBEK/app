/**
 * Folders in a workspace are the "/"-separated parts of its file paths (as in S3 and git).
 * A folder made in the panel exists only until it holds a file; the files make it durable.
 */

export interface FolderEntry {
  name: string;
  path: string;
  /** Files anywhere under it. */
  count: number;
}

export interface FolderView<F extends { path: string }> {
  folders: FolderEntry[];
  files: F[];
}

export function normalizeFolder(folder: string): string {
  return folder.split('/').map((p) => p.trim()).filter(Boolean).join('/');
}

/** What one folder holds directly: its subfolders (with file counts) and its files. */
export function folderView<F extends { path: string }>(all: F[], folder: string, extraFolders: string[] = []): FolderView<F> {
  const base = normalizeFolder(folder);
  const prefix = base ? `${base}/` : '';
  const counts = new Map<string, number>();
  const files: F[] = [];
  for (const f of all) {
    if (!f.path.startsWith(prefix)) continue;
    const rest = f.path.slice(prefix.length);
    const slash = rest.indexOf('/');
    if (slash === -1) files.push(f);
    else counts.set(rest.slice(0, slash), (counts.get(rest.slice(0, slash)) ?? 0) + 1);
  }
  for (const extra of extraFolders.map(normalizeFolder)) {
    if (!extra.startsWith(prefix) || extra === base) continue;
    const name = extra.slice(prefix.length).split('/')[0];
    if (name && !counts.has(name)) counts.set(name, 0);
  }
  return {
    folders: [...counts.entries()]
      .map(([name, count]) => ({ name, path: prefix + name, count }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    files: files.sort((a, b) => a.path.localeCompare(b.path)),
  };
}

/** The folder's ancestors, for a breadcrumb: [{name:'Workspace', path:''}, {name:'notes', path:'notes'}, ...]. */
export function breadcrumb(folder: string): { name: string; path: string }[] {
  const parts = normalizeFolder(folder).split('/').filter(Boolean);
  return [{ name: 'Workspace', path: '' }, ...parts.map((name, i) => ({ name, path: parts.slice(0, i + 1).join('/') }))];
}

/** A folder or file name the user typed: one path part, no slashes or dot-names. */
export function validName(name: string): string | null {
  const n = name.trim();
  if (!n || n === '.' || n === '..' || n.includes('/') || n.includes('\\')) return null;
  return n;
}

export function joinPath(folder: string, name: string): string {
  const base = normalizeFolder(folder);
  return base ? `${base}/${name}` : name;
}
