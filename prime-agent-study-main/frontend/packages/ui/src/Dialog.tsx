import * as RDialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import styles from "./Dialog.module.css";

export function Dialog({
  open,
  onOpenChange,
  title,
  children,
  trigger,
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  title: string;
  children: ReactNode;
  trigger?: ReactNode;
}) {
  const rootProps: RDialog.DialogProps = {};
  if (open !== undefined) rootProps.open = open;
  if (onOpenChange !== undefined) rootProps.onOpenChange = onOpenChange;

  return (
    <RDialog.Root {...rootProps}>
      {trigger ? <RDialog.Trigger asChild>{trigger}</RDialog.Trigger> : null}
      <RDialog.Portal>
        <RDialog.Overlay className={styles.overlay} />
        <RDialog.Content className={styles.content} aria-describedby={undefined}>
          <RDialog.Title className={styles.title}>{title}</RDialog.Title>
          {children}
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}
