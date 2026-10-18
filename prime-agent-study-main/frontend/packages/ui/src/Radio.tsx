import * as RadioGroup from "@radix-ui/react-radio-group";
import type { ReactNode } from "react";
import styles from "./Radio.module.css";

export function RadioGroupRoot(props: RadioGroup.RadioGroupProps) {
  return <RadioGroup.Root {...props} />;
}

export function Radio({ value, children }: { value: string; children?: ReactNode }) {
  return (
    <label className={styles.root}>
      <RadioGroup.Item className={styles.item} value={value}>
        <RadioGroup.Indicator className={styles.indicator} />
      </RadioGroup.Item>
      {children}
    </label>
  );
}

export { RadioGroup };
