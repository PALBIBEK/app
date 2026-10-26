import Link from 'next/link';
import { forwardRef } from 'react';

import { cn } from '@/lib/utils';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md';

const base =
  'inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium ' +
  'transition-colors disabled:cursor-not-allowed disabled:opacity-50';

const variants: Record<Variant, string> = {
  primary: 'bg-accent-500 text-white hover:bg-accent-600 hover:text-white',
  secondary: 'border border-line-strong bg-surface text-ink hover:bg-canvas hover:text-ink',
  ghost: 'text-ink-2 hover:bg-line-soft hover:text-ink',
  danger: 'border border-line-strong bg-surface text-bad hover:bg-red-50 hover:text-bad',
};

// 40 px default, 36 px compact: both clear the 36 px minimum, 40 the 44 px with padding.
const sizes: Record<Size, string> = {
  sm: 'h-9 px-3 text-sm',
  md: 'h-10 px-4 text-base',
};

export function buttonClass(variant: Variant = 'secondary', size: Size = 'md', className?: string): string {
  return cn(base, variants[variant], sizes[size], className);
}

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', className, type = 'button', ...props },
  ref,
) {
  return <button ref={ref} type={type} className={buttonClass(variant, size, className)} {...props} />;
});

interface LinkButtonProps extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  href: string;
  variant?: Variant;
  size?: Size;
}

/** A link that looks like a button (navigation, not an action). */
export function LinkButton({ href, variant = 'secondary', size = 'md', className, ...props }: LinkButtonProps): JSX.Element {
  return <Link href={href} className={buttonClass(variant, size, className)} {...props} />;
}
