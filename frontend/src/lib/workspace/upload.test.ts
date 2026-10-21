import { describe, expect, it, vi } from 'vitest';

import type { UploadPlan, WorkspaceFile } from '@/lib/api/endpoints/workspace';

import { type UploadDeps, uploadToWorkspace } from './upload';

const FILE: WorkspaceFile = { path: 'a.txt', content_sha256: 'ab', object_key: 'k', size_bytes: 3, version: 1, file_id: 'f' };
const blob = new Blob(['abc'], { type: 'text/plain' });

function deps(plan: UploadPlan, over: Partial<UploadDeps> = {}): UploadDeps {
  return {
    start: vi.fn(async () => plan),
    complete: vi.fn(async () => FILE),
    viaApi: vi.fn(async () => FILE),
    put: vi.fn(async () => {}),
    sha256: vi.fn(async () => 'ab'.repeat(32)),
    ...over,
  };
}

describe('uploadToWorkspace', () => {
  it('starts, PUTs to storage with the signed headers, then completes', async () => {
    const d = deps({ status: 'upload', max_bytes: 9, upload_token: 'tok', url: 'http://s3/u', method: 'PUT', headers: { 'x-amz-checksum-sha256': 'q=' } });
    expect(await uploadToWorkspace(d, 's1', 'a.txt', blob)).toBe(FILE);
    expect(d.start).toHaveBeenCalledWith('s1', { path: 'a.txt', size_bytes: 3, content_sha256: 'ab'.repeat(32), mime_type: 'text/plain' });
    expect(d.put).toHaveBeenCalledWith('http://s3/u', 'PUT', { 'x-amz-checksum-sha256': 'q=' }, blob, expect.any(Function));
    expect(d.complete).toHaveBeenCalledWith('s1', 'tok');
    expect(d.viaApi).not.toHaveBeenCalled();
  });

  it('sends nothing when the bytes are already stored', async () => {
    const d = deps({ status: 'committed', max_bytes: 9, file: FILE });
    expect(await uploadToWorkspace(d, 's1', 'a.txt', blob)).toBe(FILE);
    expect(d.put).not.toHaveBeenCalled();
    expect(d.complete).not.toHaveBeenCalled();
  });

  it('goes through the API when storage cannot take a direct upload, or the page cannot hash', async () => {
    const proxy = deps({ status: 'proxy', max_bytes: 9 });
    await uploadToWorkspace(proxy, 's1', 'a.txt', blob);
    expect(proxy.viaApi).toHaveBeenCalled();
    const insecure = deps({ status: 'upload', max_bytes: 9 }, { sha256: null });
    await uploadToWorkspace(insecure, 's1', 'a.txt', blob);
    expect(insecure.start).not.toHaveBeenCalled();
    expect(insecure.viaApi).toHaveBeenCalled();
  });

  it('does not complete when storage refused the bytes', async () => {
    const d = deps(
      { status: 'upload', max_bytes: 9, upload_token: 'tok', url: 'http://s3/u' },
      { put: vi.fn(async () => { throw new Error('Storage refused the upload (403)'); }) }
    );
    await expect(uploadToWorkspace(d, 's1', 'a.txt', blob)).rejects.toThrow('403');
    expect(d.complete).not.toHaveBeenCalled();
  });
});
