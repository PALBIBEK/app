import { cn } from '@/lib/utils';

type Tone = 'default' | 'accent';

interface CardProps extends React.HTMLAttributes<HTMLElement> {
  as?: 'section' | 'div' | 'article' | 'aside';
  tone?: Tone;
  padded?: boolean;
}

/** A white surface with a thin border; `accent` marks something that needs action. */
export function Card({ as: Tag = 'section', tone = 'default', padded = false, className, ...props }: CardProps): JSX.Element {
  return (
    <Tag
      className={cn(
        'overflow-hidden rounded-card border bg-surface',
        tone === 'accent' ? 'border-accent-500' : 'border-line',
        padded && 'p-4',
        className,
      )}
      {...props}
    />
  );
}

interface CardHeaderProps {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  icon?: React.ReactNode;
  tone?: Tone;
  className?: string;
}

export function CardHeader({ title, description, actions, icon, tone = 'default', className }: CardHeaderProps): JSX.Element {
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3',
        tone === 'accent' ? 'bg-accent-50 text-accent-700' : 'border-b border-line',
        className,
      )}
    >
      {icon ? <span aria-hidden="true" className="flex">{icon}</span> : null}
      <div className="min-w-0 flex-1">
        <h2 className={cn('text-base font-semibold', tone === 'accent' && 'text-accent-700')}>{title}</h2>
        {description ? <p className="text-sm text-ink-2">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
