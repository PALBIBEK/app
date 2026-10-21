'use client';

/**
 * A workspace scope: its files (view, download, upload, delete) and who it is shared with.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { DownloadSimple, Eye, FolderOpen, Trash, UploadSimple, UserPlus, X } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { workspaceAPI, type ScopeMember, type WorkspaceFile } from '@/lib/api/endpoints/workspace';
import { getApiErrorMessage } from '@/lib/api/errors';
import { putWithProgress, sha256Hex, uploadToWorkspace } from '@/lib/workspace/upload';
import { useAuthStore } from '@/lib/store/authStore';

const TEXT_PREVIEW_LIMIT = 200_000;

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
            file.name,
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

  async function view(path: string): Promise<void> {
    const blob = await workspaceAPI.getFile(scopeId, path);
    const text = await blob.slice(0, TEXT_PREVIEW_LIMIT).text();
    setPreview({ path, text: blob.size > TEXT_PREVIEW_LIMIT ? `${text}\n… (${size(blob.size)} in all; download for the rest)` : text });
  }

  async function download(path: string): Promise<void> {
    const blob = await workspaceAPI.getFile(scopeId, path);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = path.split('/').pop() || 'file';
    a.click();
    URL.revokeObjectURL(url);
  }

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
          <UploadSimple size={14} /> Upload
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

      {files.length ? (
        <ul data-testid="agent-workspace-file-list" className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
          {files.map((f) => (
            <li key={f.path} className="flex items-center gap-3 px-3 py-1.5">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-700">{f.path}</span>
              <span className="text-[11px] text-slate-400">v{f.version} · {size(f.size_bytes)}</span>
              <button type="button" aria-label={`View ${f.path}`} onClick={() => void view(f.path)} className="rounded p-1 text-slate-500 hover:bg-slate-100"><Eye size={14} /></button>
              <button type="button" aria-label={`Download ${f.path}`} onClick={() => void download(f.path)} className="rounded p-1 text-slate-500 hover:bg-slate-100"><DownloadSimple size={14} /></button>
              <button type="button" aria-label={`Delete ${f.path}`} disabled={disabled || busy} onClick={() => void run(() => workspaceAPI.deleteFile(scopeId, f.path), `Deleted ${f.path}`, true)} className="rounded p-1 text-slate-500 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"><Trash size={14} /></button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-slate-400">No files yet.</p>
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
