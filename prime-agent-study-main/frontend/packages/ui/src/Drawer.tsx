import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

/** Stub — side drawer for Phase 2 detail pane. */
export function Drawer({
  open,
  onOpenChange,
  title,
  children,
}: {
  open?: boolean;
  onOpenChange?: (o: boolean) => void;
  title: string;
  children: ReactNode;
}) {
  const rootProps: Dialog.DialogProps = {};
  if (open !== undefined) rootProps.open = open;
  if (onOpenChange !== undefined) rootProps.onOpenChange = onOpenChange;

  return (
    <Dialog.Root {...rootProps}>
      <Dialog.Portal>
        <Dialog.Overlay style={{ position: "fixed", inset: 0, background: "rgb(0 0 0 / 35%)", zIndex: 40 }} />
        <Dialog.Content
          style={{
            position: "fixed",
            top: 0,
            right: 0,
            height: "100%",
            width: "min(420px, 100%)",
            background: "var(--color-surface-raised)",
            borderLeft: "1px solid var(--color-border-subtle)",
            padding: "var(--space-6)",
            zIndex: 50,
          }}
        >
          <Dialog.Title style={{ fontFamily: "var(--font-display)", marginTop: 0 }}>{title}</Dialog.Title>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
