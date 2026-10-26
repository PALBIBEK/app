'use client';

/**
 * A conversation's own files, beside its workspace:
 *  - Attached: workspace files pinned to a version for this conversation. Its runs read exactly
 *    that version, read-only, in `.attachments/`. Attach one from the workspace, or upload one
 *    (it is saved in the workspace's Attachments folder, then attached).
 *  - Made by the agent: files a run left in `.outputs/`, collected when the run ended. View or
 *    download them, or save one into the workspace (a new version that records where it came from).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { DownloadSimple, Eye, FloppyDisk, Paperclip, Sparkle, UploadSimple, X } from '@phosphor-icons/react';
import { toast } from 'sonner';

import {
  sessionFilesAPI,
  workspaceAPI,
  type Artifact,
  type Attachment,
  type WorkspaceFile,
} from '@/lib/api/endpoints/workspace';
import { getApiErrorMessage } from '@/lib/api/errors';
import { joinPath } from '@/lib/workspace/tree';
import { putWithProgress, sha256Hex, uploadToWorkspace } from '@/lib/workspace/upload';

const PREVIEW_LIMIT = 200_000;
const UPLOAD_FOLDER = 'Attachments';

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function ConversationFiles({
  sessionId,
  scopeId,
  refreshKey,
  disabled,
  onWorkspaceChanged,
  className,
}: {
  sessionId: string;
  scopeId: string;
  /** Changes when a run ends: its outputs may have been collected. */
  refreshKey?: number;
  disabled?: boolean;
  /** Called after something was added to the workspace (an upload, a saved output). */
  onWorkspaceChanged?: () => void;
  /** The section's own box (borders, padding); a strip under the header by default. */
  className?: string;
}): JSX.Element {
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [picking, setPicking] = useState<WorkspaceFile[] | null>(null);
  const [saving, setSaving] = useState<{ id: string; path: string } | null>(null);
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    try {
      const [a, r] = await Promise.all([sessionFilesAPI.attachments(sessionId), sessionFilesAPI.artifacts(sessionId)]);
      setAttachments(a);
      setArtifacts(r);
    } catch {
      /* the session may not be ready yet */
    }
  }, [sessionId]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  async function act(action: () => Promise<unknown>, done?: string): Promise<boolean> {
    setBusy(true);
    try {
      await action();
      if (done) toast.success(done);
      return true;
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Failed'));
      return false;
    } finally {
      setBusy(false);
      await refresh();
    }
  }

  async function openPicker(): Promise<void> {
    if (picking) return setPicking(null);
    try {
      setPicking(await workspaceAPI.listFiles(scopeId));
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not list the workspace'));
    }
  }

  async function uploadAndAttach(file: File): Promise<void> {
    const path = joinPath(UPLOAD_FOLDER, file.name);
    const ok = await act(async () => {
      await uploadToWorkspace(
        {
          start: workspaceAPI.startUpload,
          complete: workspaceAPI.completeUpload,
          viaApi: workspaceAPI.uploadFile,
          put: putWithProgress,
          sha256: typeof crypto !== 'undefined' && crypto.subtle ? sha256Hex : null,
        },
        scopeId,
        path,
        file
      );
      await sessionFilesAPI.attach(sessionId, path);
    }, `Attached ${file.name}`);
    if (ok) onWorkspaceChanged?.();
  }

  async function view(a: Artifact): Promise<void> {
    try {
      const blob = await sessionFilesAPI.artifactContent(sessionId, a.id);
      const text = await blob.slice(0, PREVIEW_LIMIT).text();
      setPreview({ name: a.name, text: blob.size > PREVIEW_LIMIT ? `${text}\n… (${size(blob.size)} in all)` : text });
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not open it'));
    }
  }

  async function download(a: Artifact): Promise<void> {
    try {
      const blob = await sessionFilesAPI.artifactContent(sessionId, a.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = a.name.split('/').pop() || 'file';
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Could not download it'));
    }
  }

  const attachedPaths = new Set(attachments.map((a) => a.path));

  return (
    <section data-testid="conversation-files" className={className ?? 'border-b border-slate-200/80 bg-white/60 px-6 py-3 text-sm'}> <div className="flex flex-wrap items-center gap-2"> <Paperclip size={16} className="text-ink-2" /> <p className="font-semibold text-ink">This conversation</p> <p className="text-xs text-ink-2">attached files are read-only for the agent; files it makes appear here</p> <span className="ml-auto inline-flex items-center gap-1.5"> <input ref={input} type="file" className="hidden" data-testid="attach-upload" onChange={(e) => { const file = e.target.files?.[0]; if (file) void uploadAndAttach(file); e.target.value ='';
            }}
          />
          <button type="button" disabled={disabled || busy} onClick={() => void openPicker()} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-700 disabled:opacity-50">
            <Paperclip size={13} /> Attach from workspace
          </button>
          <button type="button" disabled={disabled || busy} onClick={() => input.current?.click()} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-700 disabled:opacity-50">
            <UploadSimple size={13} /> Upload and attach
          </button>
        </span>
      </div>

      {picking ? (
        <ul data-testid="attach-picker" className="mt-2 max-h-48 divide-y divide-slate-100 overflow-auto rounded-lg border border-slate-200 bg-white">
          {picking.length === 0 ? <li className="px-3 py-1.5 text-xs text-slate-400">The workspace has no files yet.</li> : null}
          {picking.map((f) => (
            <li key={f.path}>
              <button
                type="button"
                disabled={attachedPaths.has(f.path) || busy}
                onClick={() => void act(() => sessionFilesAPI.attach(sessionId, f.path), `Attached ${f.path}`).then((ok) => ok && setPicking(null))}
                aria-label={`Attach ${f.path}`}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-slate-50 disabled:opacity-40"
              >
                <span className="flex-1 truncate font-mono text-slate-700">{f.path}</span>
                <span className="text-slate-400">{attachedPaths.has(f.path) ? 'attached' : `v${f.version} · ${size(f.size_bytes)}`}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      <div data-testid="attachments" className="mt-2 flex flex-wrap items-center gap-1.5">
        {attachments.length === 0 ? <span className="text-xs text-slate-400">Nothing attached.</span> : null}
        {attachments.map((a) => (
          <span key={a.id} title={`${a.path} · version ${a.version} · the agent reads ${a.sandbox_path}`} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
            <Paperclip size={11} /> {a.name} <span className="text-slate-400">v{a.version}</span>
            <button type="button" aria-label={`Detach ${a.name}`} disabled={disabled || busy} onClick={() => void act(() => sessionFilesAPI.detach(sessionId, a.id))} className="text-slate-400 hover:text-red-600">
              <X size={11} />
            </button>
          </span>
        ))}
      </div>

      {artifacts.length ? (
        <ul data-testid="artifacts" className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
          {artifacts.map((a) => (
            <li key={a.id} className="px-3 py-1.5">
              <div className="flex items-center gap-2">
                <Sparkle size={14} weight="fill" className="text-violet-500" />
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-700">{a.name}</span>
                <span className="text-[11px] text-slate-400">{size(a.size_bytes)}</span>
                {a.saved_path ? (
                  <span data-testid="artifact-saved" className="text-[11px] text-emerald-700">saved to {a.saved_path} (v{a.saved_version})</span>
                ) : null}
                <button type="button" aria-label={`View ${a.name}`} onClick={() => void view(a)} className="rounded p-1 text-slate-500 hover:bg-slate-100"><Eye size={14} /></button>
                <button type="button" aria-label={`Download ${a.name}`} onClick={() => void download(a)} className="rounded p-1 text-slate-500 hover:bg-slate-100"><DownloadSimple size={14} /></button>
                <button type="button" aria-label={`Save ${a.name} to the workspace`} disabled={disabled || busy} onClick={() => setSaving(saving?.id === a.id ? null : { id: a.id, path: a.saved_path ?? joinPath('Agent outputs', a.name) })} className="rounded p-1 text-ink-2 hover:bg-line-soft disabled:opacity-40"><FloppyDisk size={14} /></button>
              </div>
              {saving?.id === a.id ? (
                <form
                  className="mt-1 flex items-center gap-1.5 text-xs"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const path = saving.path.trim();
                    if (!path) return;
                    void act(() => sessionFilesAPI.save(sessionId, a.id, path), `Saved to ${path}`).then((ok) => {
                      if (ok) {
                        setSaving(null);
                        onWorkspaceChanged?.();
                      }
                    });
                  }}
                >
                  <span className="text-ink-2">Save as</span>
                  <input autoFocus aria-label="Save as path" value={saving.path} onChange={(e) => setSaving({ ...saving, path: e.target.value })} className="flex-1 rounded border border-line px-1.5 py-0.5 font-mono" />
                  <button type="submit" className="rounded bg-accent-600 px-2 py-0.5 text-white">Save</button>
                </form>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {preview ? (
        <div className="mt-2 rounded-lg border border-line bg-surface">
          <div className="flex items-center justify-between border-b border-line-soft px-3 py-1.5">
            <span className="font-mono text-xs text-ink-2">{preview.name}</span>
            <button type="button" aria-label="Close preview" onClick={() => setPreview(null)} className="rounded p-1 text-ink-3 hover:bg-line-soft"><X size={14} /></button>
          </div>
          <pre data-testid="artifact-preview" className="max-h-64 overflow-auto p-3 font-mono text-[11px] text-ink">{preview.text}</pre>
        </div>
      ) : null}
    </section>
  );
}
