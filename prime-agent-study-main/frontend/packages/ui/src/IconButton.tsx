import type { ButtonHTMLAttributes } from "react";
import { Icon, type IconName } from "./Icon";
import { cx } from "./lib/cx";
import styles from "./IconButton.module.css";

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: IconName;
  "aria-label": string;
}

export function IconButton({ icon, className, ...rest }: IconButtonProps) {
  return (
    <button type="button" className={cx(styles.root, className)} {...rest}>
      <Icon name={icon} size={18} />
    </button>
  );
}
