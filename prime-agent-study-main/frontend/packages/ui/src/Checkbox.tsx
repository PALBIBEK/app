import * as RCheckbox from "@radix-ui/react-checkbox";
import { Check, Minus } from "lucide-react";
import type { ReactNode } from "react";
import styles from "./Checkbox.module.css";

export interface CheckboxProps {
  checked?: boolean | "indeterminate";
  onCheckedChange?: (v: boolean | "indeterminate") => void;
  disabled?: boolean;
  id?: string;
  children?: ReactNode;
}

export function Checkbox({ children, ...props }: CheckboxProps) {
  return (
    <label className={styles.root}>
      <RCheckbox.Root className={styles.box} {...props}>
        <RCheckbox.Indicator>
          {props.checked === "indeterminate" ? <Minus size={12} /> : <Check size={12} />}
        </RCheckbox.Indicator>
      </RCheckbox.Root>
      {children}
    </label>
  );
}
