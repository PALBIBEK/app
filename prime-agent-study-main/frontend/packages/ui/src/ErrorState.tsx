import type { AppError } from "@yal/core";
import type { ReactNode } from "react";
import { Button } from "./Button";
import styles from "./ErrorState.module.css";

function copy(error: AppError): { title: string; body: string; meta?: string } {
  switch (error.kind) {
    case "network":
      return { title: "Network error", body: "Check your connection and try again." };
    case "auth":
      return { title: "Sign in required", body: "Your session expired." };
    case "permission":
      return { title: "Permission denied", body: `Missing: ${error.missing}` };
    case "validation":
      return { title: "Invalid input", body: "Fix the highlighted fields and retry." };
    case "notFound":
      return { title: "Not found", body: `${error.resource} was not found.` };
    case "rateLimit":
      return { title: "Rate limited", body: `Retry after ${error.retryAfter}s.` };
    case "budgetExceeded":
      return { title: "Budget exceeded", body: `Limit ${error.limit} (current ${error.current}).` };
    case "server": {
      const out: { title: string; body: string; meta?: string } = {
        title: "Server error",
        body: error.retryable ? "Something went wrong. You can retry." : "Something went wrong.",
      };
      if (error.requestId !== undefined) out.meta = `Request ID: ${error.requestId}`;
      return out;
    }
  }
}

export function ErrorState({
  error,
  onRetry,
  action,
}: {
  error: AppError;
  onRetry?: () => void;
  action?: ReactNode;
}) {
  const c = copy(error);
  return (
    <div className={styles.root} role="alert" data-kind={error.kind}>
      <h3 className={styles.title}>{c.title}</h3>
      <p className={styles.body}>{c.body}</p>
      {c.meta ? <p className={styles.meta}>{c.meta}</p> : null}
      {onRetry ? <Button variant="secondary" onClick={onRetry}>Retry</Button> : null}
      {action}
    </div>
  );
}
