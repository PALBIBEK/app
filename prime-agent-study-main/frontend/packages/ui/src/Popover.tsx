import * as RPopover from "@radix-ui/react-popover";
import type { ReactNode } from "react";

/** Stub — minimal Radix wrapper; styling expands in Phase 2. */
export function Popover({ trigger, children }: { trigger: ReactNode; children: ReactNode }) {
  return (
    <RPopover.Root>
      <RPopover.Trigger asChild>{trigger}</RPopover.Trigger>
      <RPopover.Portal>
        <RPopover.Content
          sideOffset={6}
          style={{
            background: "var(--color-surface-raised)",
            border: "1px solid var(--color-border-subtle)",
            borderRadius: "var(--radius-md)",
            padding: "var(--space-3)",
            zIndex: 50,
          }}
        >
          {children}
        </RPopover.Content>
      </RPopover.Portal>
    </RPopover.Root>
  );
}
