/**
 * The Markdown a report's text is written in, drawn as React elements and never as HTML: an
 * agent's text is untrusted. Headings, paragraphs, bullet and numbered lists, **bold**,
 * *italic*, `code` and http(s) links; anything else shows as the text it is.
 */

import type { ReactNode } from 'react';

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|\*[^*\n]+\*|_[^_\n]+_)/g;
const LINK = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/;
const BULLET = /^\s*[-*]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^(#{1,6})\s+(.*)$/;

function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    if (i % 2 === 0) return part;
    const link = part.match(LINK);
    if (link) {
      return (
        <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer nofollow" className="text-accent-700 underline underline-offset-2">
          {link[1]}
        </a>
      );
    }
    if (part.startsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`')) return <code key={i} className="rounded bg-canvas px-1 font-mono text-[0.92em]">{part.slice(1, -1)}</code>;
    return <em key={i}>{part.slice(1, -1)}</em>;
  });
}

type Block = { kind: 'p' | 'h'; lines: string[]; level?: number } | { kind: 'ul' | 'ol'; lines: string[] };

function blocks(markdown: string): Block[] {
  const out: Block[] = [];
  for (const line of markdown.split('\n')) {
    const last = out[out.length - 1];
    const heading = line.match(HEADING);
    const bullet = line.match(BULLET);
    const numbered = line.match(NUMBERED);
    if (!line.trim()) out.push({ kind: 'p', lines: [] });
    else if (heading) out.push({ kind: 'h', lines: [heading[2]], level: heading[1].length });
    else if (bullet || numbered) {
      const kind = bullet ? 'ul' : 'ol';
      const item = (bullet ?? numbered)![1];
      if (last?.kind === kind) last.lines.push(item);
      else out.push({ kind, lines: [item] });
    } else if (last?.kind === 'p') last.lines.push(line);
    else out.push({ kind: 'p', lines: [line] });
  }
  return out.filter((b) => b.lines.length > 0);
}

export function Markdown({ markdown }: { markdown: string }): JSX.Element {
  return (
    <span className="flex flex-col gap-1.5">
      {blocks(markdown).map((block, i) => {
        switch (block.kind) {
          case 'h':
            return (
              <strong key={i} className={block.level === 1 ? 'text-base' : 'text-[14px]'}>
                {inline(block.lines[0])}
              </strong>
            );
          case 'ul':
          case 'ol': {
            const List = block.kind;
            return (
              <List key={i} className={List === 'ul' ? 'list-disc pl-5' : 'list-decimal pl-5'}>
                {block.lines.map((item, j) => (
                  <li key={j}>{inline(item)}</li>
                ))}
              </List>
            );
          }
          default:
            return (
              <span key={i} className="block">
                {block.lines.map((line, j) => (
                  <span key={j}>
                    {j > 0 ? <br /> : null}
                    {inline(line)}
                  </span>
                ))}
              </span>
            );
        }
      })}
    </span>
  );
}
