import * as Dropdown from "@radix-ui/react-dropdown-menu";
import type { ReactNode } from "react";
import styles from "./Menu.module.css";

export function Menu({
  trigger,
  items,
}: {
  trigger: ReactNode;
  items: Array<{ label: string; onSelect?: () => void; danger?: boolean }>;
}) {
  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>{trigger}</Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content className={styles.content} sideOffset={6}>
          {items.map((item) => {
            const itemProps: Dropdown.DropdownMenuItemProps = {
              className: styles.item,
            };
            if (item.onSelect !== undefined) {
              itemProps.onSelect = () => item.onSelect?.();
            }
            return (
              <Dropdown.Item key={item.label} {...itemProps}>
                {item.label}
              </Dropdown.Item>
            );
          })}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}
