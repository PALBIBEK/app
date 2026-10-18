import { Slot } from "@radix-ui/react-slot";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "./lib/cx";
import { Spinner } from "./Spinner";
import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
  asChild?: boolean;
  children?: ReactNode;
}

export function Button({
  variant = "primary",
  loading = false,
  asChild = false,
  className,
  disabled,
  children,
  ...rest
}: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp
      className={cx(styles.root, styles[variant], loading && styles.loading, className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Spinner size="sm" /> : null}
      {children}
    </Comp>
  );
}
