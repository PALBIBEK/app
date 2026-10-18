import {
  Command,
  Menu,
  Moon,
  PanelLeft,
  Search,
  Sun,
  type LucideProps,
} from "lucide-react";

const registry = {
  command: Command,
  menu: Menu,
  moon: Moon,
  "panel-left": PanelLeft,
  search: Search,
  sun: Sun,
} as const;

export type IconName = keyof typeof registry;

export function Icon({
  name,
  ...props
}: { name: IconName } & Omit<LucideProps, "ref">) {
  const Cmp = registry[name];
  return <Cmp aria-hidden={props["aria-label"] ? undefined : true} {...props} />;
}
