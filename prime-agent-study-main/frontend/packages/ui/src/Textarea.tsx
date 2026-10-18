import type { TextareaHTMLAttributes } from "react";
import { cx } from "./lib/cx";
import styles from "./Textarea.module.css";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
}

export function Textarea({ label, className, id, ...rest }: TextareaProps) {
  const el = <textarea id={id} className={cx(styles.control, className)} {...rest} />;
  if (!label) return el;
  return (
    <label className={styles.field} htmlFor={id}>
      {label}
      {el}
    </label>
  );
}
