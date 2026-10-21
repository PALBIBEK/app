/**
 * Upload a file into an agent workspace the way production apps do: the browser sends the
 * bytes straight to object storage over a short-lived signed URL, so they never pass through
 * the API servers. Three steps:
 *
 *   1. start    – tell the API the path, size, type and SHA-256; it checks access and limits
 *                 and answers with a signed URL (or "committed" when those bytes are stored)
 *   2. PUT      – send the file to that URL with the signed headers (progress is reported)
 *   3. complete – the API checks what arrived against the SHA-256 and records the file
 *
 * When storage cannot hand out URLs ("proxy"), or the page cannot hash files (crypto.subtle
 * exists only on https or localhost), the file goes through the API instead.
 */

import type { UploadPlan, UploadStart, WorkspaceFile } from '@/lib/api/endpoints/workspace';

export interface UploadDeps {
  start: (scopeId: string, body: UploadStart) => Promise<UploadPlan>;
  complete: (scopeId: string, token: string) => Promise<WorkspaceFile>;
  viaApi: (scopeId: string, path: string, file: Blob, mime: string | undefined, onProgress: (f: number) => void) => Promise<WorkspaceFile>;
  put: (url: string, method: string, headers: Record<string, string>, file: Blob, onProgress: (f: number) => void) => Promise<void>;
  sha256: ((file: Blob) => Promise<string>) | null;
}

export async function sha256Hex(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

/** PUT to storage with progress (fetch cannot report upload progress; XMLHttpRequest can). */
export function putWithProgress(
  url: string,
  method: string,
  headers: Record<string, string>,
  file: Blob,
  onProgress: (fraction: number) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`Storage refused the upload (${xhr.status})${xhr.status === 403 ? ': the link expired or the file changed' : ''}`));
    xhr.onerror = () => reject(new Error('Could not reach storage to upload the file (network or CORS)'));
    xhr.send(file);
  });
}

export async function uploadToWorkspace(
  deps: UploadDeps,
  scopeId: string,
  path: string,
  file: Blob,
  onProgress: (fraction: number) => void = () => {}
): Promise<WorkspaceFile> {
  const mime = file.type || undefined;
  if (!deps.sha256) return deps.viaApi(scopeId, path, file, mime, onProgress);

  const plan = await deps.start(scopeId, {
    path,
    size_bytes: file.size,
    content_sha256: await deps.sha256(file),
    ...(mime ? { mime_type: mime } : {}),
  });
  if (plan.status === 'committed' && plan.file) {
    onProgress(1);
    return plan.file;
  }
  if (plan.status === 'proxy') return deps.viaApi(scopeId, path, file, mime, onProgress);
  if (!plan.url || !plan.upload_token) throw new Error('The server did not say where to upload the file');

  await deps.put(plan.url, plan.method || 'PUT', plan.headers ?? {}, file, onProgress);
  return deps.complete(scopeId, plan.upload_token);
}
