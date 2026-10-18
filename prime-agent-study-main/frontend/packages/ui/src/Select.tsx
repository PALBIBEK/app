import * as RSelect from "@radix-ui/react-select";
import { ChevronDown } from "lucide-react";
import styles from "./Select.module.css";

export function Select(props: {
  value?: string;
  onValueChange?: (v: string) => void;
  placeholder?: string;
  options: Array<{ value: string; label: string }>;
  disabled?: boolean;
}) {
  const rootProps: RSelect.SelectProps = {};
  if (props.value !== undefined) rootProps.value = props.value;
  if (props.onValueChange !== undefined) rootProps.onValueChange = props.onValueChange;
  if (props.disabled !== undefined) rootProps.disabled = props.disabled;

  return (
    <RSelect.Root {...rootProps}>
      <RSelect.Trigger className={styles.trigger}>
        <RSelect.Value placeholder={props.placeholder} />
        <ChevronDown size={16} />
      </RSelect.Trigger>
      <RSelect.Portal>
        <RSelect.Content className={styles.content} position="popper">
          <RSelect.Viewport>
            {props.options.map((o) => (
              <RSelect.Item key={o.value} value={o.value} className={styles.item}>
                <RSelect.ItemText>{o.label}</RSelect.ItemText>
              </RSelect.Item>
            ))}
          </RSelect.Viewport>
        </RSelect.Content>
      </RSelect.Portal>
    </RSelect.Root>
  );
}
