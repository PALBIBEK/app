import type { ReactNode } from "react";
import styles from "./EmptyState.module.css";

export type EmptyStateVariant = "no-data" | "no-match";

export function EmptyState({
  variant,
  title,
  body,
  action,
}: {
  variant: EmptyStateVariant;
  title: string;
  body?: string;
  action?: ReactNode;
}) {
  return (
    <div className={styles.root} data-variant={variant}>
      <h3 className={styles.title}>{title}</h3>
      {body ? <p className={styles.body}>{body}</p> : null}
      {action}
    </div>
  );
}
