import type { InputHTMLAttributes } from "react";
import { cx } from "./lib/cx";
import styles from "./Input.module.css";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

export function Input({ label, className, id, ...rest }: InputProps) {
  const input = (
    <input id={id} className={cx(styles.control, className)} {...rest} />
  );
  if (!label) return input;
  return (
    <label className={styles.field} htmlFor={id}>
      {label}
      {input}
    </label>
  );
}
