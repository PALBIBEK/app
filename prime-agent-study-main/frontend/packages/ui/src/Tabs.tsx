import * as RTabs from "@radix-ui/react-tabs";
import type { ReactNode } from "react";
import styles from "./Tabs.module.css";

export function Tabs({
  tabs,
  defaultValue,
}: {
  defaultValue?: string;
  tabs: Array<{ value: string; label: string; content: ReactNode }>;
}) {
  const first = tabs[0]?.value;
  const rootProps: RTabs.TabsProps = {};
  const dv = defaultValue ?? first;
  if (dv !== undefined) rootProps.defaultValue = dv;

  return (
    <RTabs.Root {...rootProps}>
      <RTabs.List className={styles.list}>
        {tabs.map((t) => (
          <RTabs.Trigger key={t.value} value={t.value} className={styles.trigger}>
            {t.label}
          </RTabs.Trigger>
        ))}
      </RTabs.List>
      {tabs.map((t) => (
        <RTabs.Content key={t.value} value={t.value} className={styles.content}>
          {t.content}
        </RTabs.Content>
      ))}
    </RTabs.Root>
  );
}
