import type { CSSProperties } from "react";
import { cx } from "./lib/cx";
import styles from "./Skeleton.module.css";

export function Skeleton({
  width = "100%",
  height = "1rem",
  className,
}: {
  width?: string | number;
  height?: string | number;
  className?: string;
}) {
  const style: CSSProperties = { width, height };
  return <span className={cx(styles.root, className)} style={style} aria-hidden />;
}
