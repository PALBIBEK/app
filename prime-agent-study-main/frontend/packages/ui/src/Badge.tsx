import type { HTMLAttributes } from "react";
import { cx } from "./lib/cx";
import styles from "./Badge.module.css";

export function Badge({
  tone = "neutral",
  className,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: "neutral" | "success" | "warning" | "danger" | "info" }) {
  return <span className={cx(styles.root, tone !== "neutral" && styles[tone], className)} {...rest} />;
}
