import { cx } from "./lib/cx";
import styles from "./Spinner.module.css";

export function Spinner({ size = "md", className }: { size?: "sm" | "md" | "lg"; className?: string }) {
  return <span className={cx(styles.root, size !== "md" && styles[size], className)} role="status" aria-label="Loading" />;
}
