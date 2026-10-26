/**
 * Artifacts: the files runs made for the user (from their `.outputs/` folder), across the
 * user's recent sessions; download one, or open its session to save it to the workspace.
 */

'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { Archive, DownloadSimple } from '@phosphor-icons/react';
import { toast } from 'sonner';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { Button, Card, EmptyState, Mono, PageHeader, SearchInput, SkeletonRows, Table, Td, Th, Tr } from '@/components/ui';
import { sessionFilesAPI, type Artifact } from '@/lib/api/endpoints/workspace';
import { getApiErrorMessage } from '@/lib/api/errors';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useSessions } from '@/lib/api/hooks/useSessions';
import type { Session } from '@/lib/api/types/session.types';
import { formatRelativeTime } from '@/lib/utils';

const RECENT = 30;

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

async function download(sessionId: string, a: Artifact): Promise<void> {
  try {
    const blob = await sessionFilesAPI.artifactContent(sessionId, a.id);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = a.name;
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    toast.error(getApiErrorMessage(err, 'Could not download it'));
  }
}

export default function ArtifactsPage(): JSX.Element {
  const { data, isLoading } = useSessions({ page: 1, page_size: RECENT });
  const { data: harnesses } = useHarnesses();
  const [query, setQuery] = useState('');
  const sessions = useMemo(() => (data?.data ?? []).filter((s) => s.scope_id), [data]);
  const results = useQueries({
    queries: sessions.map((s) => ({
      queryKey: ['artifacts', s.id],
      queryFn: () => sessionFilesAPI.artifacts(s.id),
      staleTime: 30_000,
      retry: false,
    })),
  });
  const names = useMemo(() => new Map((harnesses?.data ?? []).map((h) => [h.id, h.name])), [harnesses]);

  const rows = useMemo(() => {
    const out: { a: Artifact; s: Session }[] = [];
    results.forEach((r, i) => {
      for (const a of r.data ?? []) out.push({ a, s: sessions[i] });
    });
    const q = query.trim().toLowerCase();
    return out
      .filter(({ a, s }) => !q || a.name.toLowerCase().includes(q) || (s.title ?? '').toLowerCase().includes(q))
      .sort((x, y) => y.a.created_at.localeCompare(x.a.created_at));
  }, [results, sessions, query]);
  const loading = isLoading || results.some((r) => r.isLoading);

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Artifacts"
          description={`Files runs made for you, from your ${RECENT} most recent sessions. Save one into a workspace from its session.`}
        />
        <div className="flex">
          <SearchInput value={query} onChange={setQuery} label="Search artifacts" placeholder="Search by file or session" />
        </div>
        <Card aria-label="Artifacts">
          {loading && rows.length === 0 ? (
            <SkeletonRows rows={4} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<Archive size={28} />}
              title={query ? 'Nothing matches' : 'No artifacts yet'}
              description="When a run writes a file to .outputs/, it is collected here as the run ends."
            />
          ) : (
            <Table minWidth={760} label="Artifacts">
              <thead>
                <tr>
                  <Th>File</Th>
                  <Th>Session</Th>
                  <Th align="right">Size</Th>
                  <Th>Saved to</Th>
                  <Th>Made</Th>
                  <Th>
                    <span className="sr-only">Download</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ a, s }) => (
                  <Tr key={a.id}>
                    <Td>
                      <Mono className="text-[13px] font-medium text-ink">{a.name}</Mono>
                      <span className="block font-mono text-xs text-ink-3">{a.content_sha256.slice(0, 12)}</span>
                    </Td>
                    <Td>
                      <Link href={`/sessions/${s.id}`} className="text-ink hover:text-accent-700">
                        {s.title || `${names.get(s.harness_id) ?? 'Agent'} session`}
                      </Link>
                    </Td>
                    <Td mono align="right">
                      {size(a.size_bytes)}
                    </Td>
                    <Td>{a.saved_path ? <Mono chip className="text-xs">{`${a.saved_path} v${a.saved_version ?? ''}`}</Mono> : <span className="text-[13px] text-ink-3">not saved</span>}</Td>
                    <Td className="whitespace-nowrap text-ink-2">{formatRelativeTime(a.created_at)}</Td>
                    <Td align="right">
                      <Button size="sm" variant="ghost" onClick={() => void download(s.id, a)} aria-label={`Download ${a.name}`}>
                        <DownloadSimple size={15} />
                      </Button>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </PageBody>
    </AppShell>
  );
}
