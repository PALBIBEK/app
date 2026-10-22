'use client';

/**
 * A workspace scope: its files in folders (view, download, upload, delete), each file's saved
 * versions (view, download, restore), and who it is shared with.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowCounterClockwise, CaretRight, ClockCounterClockwise, DownloadSimple, Eye, Folder, FolderOpen, FolderPlus, Trash, UploadSimple, UserPlus, X } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { workspaceAPI, type FileVersion, type ScopeMember, type WorkspaceFile } from '@/lib/api/endpoints/workspace';
import { getApiErrorMessage } from '@/lib/api/errors';
import { breadcrumb, folderView, joinPath, validName } from '@/lib/workspace/tree';
import { putWithProgress, sha256Hex, uploadToWorkspace } from '@/lib/workspace/upload';
import { useAuthStore } from '@/lib/store/authStore';

const TEXT_PREVIEW_LIMIT = 200_000;

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function WorkspacePanel({
  scopeId,
  sessionId,
  disabled,
  refreshKey,
}: {
  scopeId: string;
  /** A live session on this scope: reloaded after a change so the agent sees it. */
  sessionId?: string;
  disabled?: boolean;
  refreshKey?: number;
}): JSX.Element {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [members, setMembers] = useState<ScopeMember[]>([]);
  const [preview, setPreview] = useState<{ path: string; text: string } | null>(null);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState<{ name: string; fraction: number } | null>(null);
  const [folder, setFolder] = useState('');
  const [madeFolders, setMadeFolders] = useState<string[]>([]);
  const [newFolder, setNewFolder] = useState<string | null>(null);
  const [history, setHistory] = useState<{ path: string; versions: FileVersion[] } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const me = useAuthStore((st) => st.user?.id);

  const refresh = useCallback(async () => {
    if (!scopeId) return;
    try {
      const [f, m] = await Promise.all([workspaceAPI.listFiles(scopeId), workspaceAPI.listMembers(scopeId)]);
      setFiles(f);
      setMembers(m);
    } catch {
      /* the scope may not exist yet */
    }
  }, [scopeId]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  async function run(action: () => Promise<unknown>, done?: string, changesFiles = false): Promise<void> {
    setBusy(true);
    try {
      await action();
      if (done) toast.success(done);
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Failed'));
      return;
    } finally {
      setBusy(false);
      await refresh(); // show what is there now, whatever happened
    }
    // The session's sandbox (if it has one yet) holds a copy of the workspace: refresh it so
    // the agent sees the change. A failure here does not undo the change, so it only warns.
    if (changesFiles && sessionId) {
      try {
        await workspaceAPI.rehydrateSession(sessionId);
      } catch (err) {
        toast.warning(`Saved, but the running agent may not see it until its next turn: ${getApiErrorMessage(err, 'refresh failed')}`);
      }
    }
  }

  async function upload(file: File): Promise<void> {
    setUploading({ name: file.name, fraction: 0 });
    try {
      await run(
        () =>
          uploadToWorkspace(
            {
              start: workspaceAPI.startUpload,
              complete: workspaceAPI.completeUpload,
              viaApi: workspaceAPI.uploadFile,
              put: putWithProgress,
              sha256: typeof crypto !== 'undefined' && crypto.subtle ? sha256Hex : null,
            },
            scopeId,
            joinPath(folder, file.name),
            file,
            (fraction) => setUploading({ name: file.name, fraction })
          ),
        `Uploaded ${file.name}`,
        true
      );
    } finally {
      setUploading(null);
    }
  }

  async function view(path: string, version?: number): Promise<void> {
    try {
      const blob = version ? await workspaceAPI.getVersion(scopeId, path, version) : await workspaceAPI.getFile(scopeId, path);
      const text = await blob.slice(0, TEXT_PREVIEW_LIMIT).text();
      const label = version ? `${path} (version ${version})` : path;
      setPreview({ path: label, text: blob.size > TEXT_PREVIEW_LIMIT ? `${text}\n… (${size(blob.size)} in all; download for the rest)` : text });
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not open the file'));
    }
  }

  async function download(path: string, version?: number): Promise<void> {
    try {
      const blob = version ? await workspaceAPI.getVersion(scopeId, path, version) : await workspaceAPI.getFile(scopeId, path);
      const name = path.split('/').pop() || 'file';
      saveBlob(blob, version ? name.replace(/(\.[^.]*)?$/, `.v${version}$1`) : name);
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not download the file'));
    }
  }

  async function showHistory(path: string): Promise<void> {
    if (history?.path === path) {
      setHistory(null);
      return;
    }
    try {
      setHistory({ path, versions: await workspaceAPI.listVersions(scopeId, path) });
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not load the history'));
    }
  }

  async function restore(path: string, version: number): Promise<void> {
    await run(() => workspaceAPI.restoreVersion(scopeId, path, version), `Restored version ${version} of ${path.split('/').pop()}`, true);
    if (history?.path === path) setHistory({ path, versions: await workspaceAPI.listVersions(scopeId, path).catch(() => []) });
  }

  const here = folderView(files, folder, madeFolders);
  const empty = here.folders.length === 0 && here.files.length === 0;

  return (
    <section data-testid="workspace-panel" className="border-b border-slate-200/80 bg-slate-50/80 px-6 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <FolderOpen size={18} className="text-slate-500" />
        <p className="font-semibold text-slate-800">Workspace</p>
        <p className="text-xs text-slate-500">{scopeId}</p>
        <input
          ref={input}
          type="file"
          className="hidden"
          data-testid="agent-workspace-upload"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
            e.target.value = '';
          }}
        />
        <button type="button" disabled={disabled || busy || !scopeId} onClick={() => input.current?.click()} className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">
          <UploadSimple size={14} /> {folder ? `Upload to ${folder.split('/').pop()}` : 'Upload'}
        </button>
      </div>

      {uploading ? (
        <div data-testid="upload-progress" className="mt-2 flex items-center gap-3 text-xs text-slate-600">
          <span className="truncate font-mono">{uploading.name}</span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200">
            <div className="h-full rounded-full bg-primary-500 transition-all" style={{ width: `${Math.round(uploading.fraction * 100)}%` }} />
          </div>
          <span>{Math.round(uploading.fraction * 100)}%</span>
        </div>
      ) : null}

      <nav aria-label="Folder" data-testid="workspace-breadcrumb" className="mt-2 flex flex-wrap items-center gap-1 text-xs text-slate-500">
        {breadcrumb(folder).map((c, i, all) => (
          <span key={c.path} className="inline-flex items-center gap-1">
            {i > 0 ? <CaretRight size={10} className="text-slate-300" /> : null}
            {i === all.length - 1 ? (
              <span className="font-medium text-slate-700">{c.name}</span>
            ) : (
              <button type="button" onClick={() => setFolder(c.path)} className="hover:text-primary-600 hover:underline">{c.name}</button>
            )}
          </span>
        ))}
        {newFolder === null ? (
          <button type="button" disabled={disabled} onClick={() => setNewFolder('')} className="ml-2 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-slate-500 hover:bg-slate-100 disabled:opacity-40">
            <FolderPlus size={13} /> New folder
          </button>
        ) : (
          <form
            className="ml-2 inline-flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              const name = validName(newFolder);
              if (!name) return void toast.error('A folder name is one name, without "/"');
              const path = joinPath(folder, name);
              setMadeFolders((m) => (m.includes(path) ? m : [...m, path]));
              setNewFolder(null);
              setFolder(path);
            }}
          >
            <input autoFocus value={newFolder} onChange={(e) => setNewFolder(e.target.value)} aria-label="New folder name" placeholder="folder name" className="w-32 rounded border border-slate-200 bg-white px-1.5 py-0.5 text-xs" />
            <button type="submit" className="rounded border border-slate-200 bg-white px-1.5 py-0.5">Create</button>
            <button type="button" onClick={() => setNewFolder(null)} aria-label="Cancel new folder" className="text-slate-400"><X size={12} /></button>
          </form>
        )}
      </nav>

      {empty ? (
        <p className="mt-2 text-xs text-slate-400">
          {folder ? 'Empty folder: upload a file to keep it.' : 'No files yet.'}
        </p>
      ) : (
        <ul data-testid="agent-workspace-file-list" className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
          {here.folders.map((d) => (
            <li key={`dir:${d.path}`}>
              <button type="button" onClick={() => setFolder(d.path)} aria-label={`Open folder ${d.name}`} className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-slate-50">
                <Folder size={15} weight="fill" className="text-amber-400" />
                <span className="flex-1 truncate text-xs font-medium text-slate-700">{d.name}</span>
                <span className="text-[11px] text-slate-400">{d.count ? `${d.count} file${d.count === 1 ? '' : 's'}` : 'empty'}</span>
              </button>
            </li>
          ))}
          {here.files.map((f) => {
            const name = f.path.split('/').pop() ?? f.path;
            return (
              <li key={f.path}>
                <div className="flex items-center gap-3 px-3 py-1.5">
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-700" title={f.path}>{name}</span>
                  <span className="text-[11px] text-slate-400">v{f.version} · {size(f.size_bytes)}</span>
                  <button type="button" aria-label={`View ${f.path}`} onClick={() => void view(f.path)} className="rounded p-1 text-slate-500 hover:bg-slate-100"><Eye size={14} /></button>
                  <button type="button" aria-label={`Download ${f.path}`} onClick={() => void download(f.path)} className="rounded p-1 text-slate-500 hover:bg-slate-100"><DownloadSimple size={14} /></button>
                  <button type="button" aria-label={`History of ${f.path}`} aria-expanded={history?.path === f.path} onClick={() => void showHistory(f.path)} className={`rounded p-1 hover:bg-slate-100 ${history?.path === f.path ? 'text-primary-600' : 'text-slate-500'}`}><ClockCounterClockwise size={14} /></button>
                  <button type="button" aria-label={`Delete ${f.path}`} disabled={disabled || busy} onClick={() => void run(() => workspaceAPI.deleteFile(scopeId, f.path), `Deleted ${f.path}`, true)} className="rounded p-1 text-slate-500 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"><Trash size={14} /></button>
                </div>
                {history?.path === f.path ? (
                  <ol data-testid="file-history" className="mx-3 mb-2 space-y-1 rounded-md bg-slate-50 p-2">
                    {history.versions.map((v) => (
                      <li key={v.version} className="flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
                        <span className="font-medium text-slate-800">Version {v.version}</span>
                        {v.current ? <span className="rounded bg-primary-100 px-1 text-primary-700">current</span> : null}
                        <span>{ago(v.created_at)}</span>
                        <span>{v.source?.startsWith('run:') ? 'by the agent' : v.author === me ? 'by you' : v.author_email ? `by ${v.author_email}` : ''}</span>
                        {v.source?.startsWith('artifact:') ? <span className="rounded bg-violet-50 px-1 text-violet-700">saved from the agent&apos;s output</span> : null}
                        {v.restored_from ? <span>restored from version {v.restored_from}</span> : null}
                        <span className="text-slate-400">{size(v.size_bytes)}</span>
                        <span className="ml-auto inline-flex items-center gap-1">
                          <button type="button" aria-label={`View version ${v.version}`} onClick={() => void view(f.path, v.version)} className="rounded p-0.5 text-slate-500 hover:bg-white"><Eye size={13} /></button>
                          <button type="button" aria-label={`Download version ${v.version}`} onClick={() => void download(f.path, v.version)} className="rounded p-0.5 text-slate-500 hover:bg-white"><DownloadSimple size={13} /></button>
                          {v.current ? null : (
                            <button type="button" aria-label={`Restore version ${v.version}`} disabled={disabled || busy} onClick={() => void restore(f.path, v.version)} className="inline-flex items-center gap-0.5 rounded border border-slate-200 bg-white px-1.5 py-0.5 text-slate-700 hover:border-primary-300 disabled:opacity-40">
                              <ArrowCounterClockwise size={11} /> Restore
                            </button>
                          )}
                        </span>
                      </li>
                    ))}
                  </ol>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      {preview ? (
        <div className="mt-2 rounded-lg border border-slate-200 bg-white">
          <div className="flex items-center justify-between border-b border-slate-100 px-3 py-1.5">
            <span className="font-mono text-xs text-slate-600">{preview.path}</span>
            <button type="button" aria-label="Close preview" onClick={() => setPreview(null)} className="rounded p-1 text-slate-400 hover:bg-slate-100"><X size={14} /></button>
          </div>
          <pre data-testid="file-preview" className="max-h-64 overflow-auto p-3 font-mono text-[11px] text-slate-800">{preview.text}</pre>
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-slate-600">Shared with</span>
        {members.map((m) => (
          <span key={m.principal_id} className="inline-flex items-center gap-1 rounded-full bg-white px-2 py-0.5 text-xs text-slate-700 ring-1 ring-slate-200">
            {m.principal_id === me ? 'you' : m.email ?? m.principal_id.slice(0, 8)}
            {m.principal_id === me ? null : <button type="button" aria-label={`Remove ${m.email ?? m.principal_id}`} disabled={disabled || busy} onClick={() => void run(() => workspaceAPI.removeMember(scopeId, m.principal_id), 'Removed')} className="text-slate-400 hover:text-red-600">
              <X size={11} />
            </button>}
          </span>
        ))}
        <form
          className="inline-flex items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            const who = email.trim();
            if (who) void run(() => workspaceAPI.inviteMember(scopeId, who), `Shared with ${who}`).then(() => setEmail(''));
          }}
        >
          <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="email to share with" aria-label="Email to share with" className="w-44 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs" />
          <button type="submit" disabled={disabled || busy || !email.trim()} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 disabled:opacity-50">
            <UserPlus size={12} /> Share
          </button>
        </form>
      </div>
    </section>
  );
}
