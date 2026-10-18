import * as RSwitch from "@radix-ui/react-switch";
import styles from "./Switch.module.css";

export function Switch(props: RSwitch.SwitchProps) {
  return (
    <RSwitch.Root className={styles.root} {...props}>
      <RSwitch.Thumb className={styles.thumb} />
    </RSwitch.Root>
  );
}
