import { cn } from '@/lib/utils';

/** A table that scrolls sideways in its own box on narrow screens. */
export function Table({
  children,
  minWidth = 760,
  label,
  className,
}: {
  children: React.ReactNode;
  minWidth?: number;
  label?: string;
  className?: string;
}): JSX.Element {
  return (
    <div className="overflow-x-auto">
      <table aria-label={label} className={cn('w-full border-collapse text-sm', className)} style={{ minWidth }}>
        {children}
      </table>
    </div>
  );
}

export function Th({
  children,
  align = 'left',
  className,
}: {
  children?: React.ReactNode;
  align?: 'left' | 'right';
  className?: string;
}): JSX.Element {
  return (
    <th
      scope="col"
      className={cn(
        'border-b border-line px-4 py-3 text-xs font-medium text-ink-3',
        align === 'right' ? 'text-right' : 'text-left',
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  align = 'left',
  mono,
  className,
}: {
  children?: React.ReactNode;
  align?: 'left' | 'right';
  mono?: boolean;
  className?: string;
}): JSX.Element {
  return (
    <td
      className={cn(
        'border-b border-line-soft px-4 py-3 align-middle',
        align === 'right' && 'text-right',
        mono && 'tabular whitespace-nowrap font-mono text-[12.5px]',
        className,
      )}
    >
      {children}
    </td>
  );
}

export function Tr({ children, className }: { children: React.ReactNode; className?: string }): JSX.Element {
  return <tr className={cn('transition-colors hover:bg-canvas/60', className)}>{children}</tr>;
}
