import * as RAvatar from "@radix-ui/react-avatar";

/** Stub */
export function Avatar({ name, src }: { name: string; src?: string }) {
  const initials = name
    .split(" ")
    .map((p) => p[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <RAvatar.Root
      style={{
        display: "inline-flex",
        width: 32,
        height: 32,
        borderRadius: 999,
        background: "var(--color-accent-muted)",
        color: "var(--color-accent-default)",
        alignItems: "center",
        justifyContent: "center",
        fontSize: "var(--text-small)",
        fontWeight: 600,
        overflow: "hidden",
      }}
    >
      {src ? <RAvatar.Image src={src} alt={name} /> : null}
      <RAvatar.Fallback delayMs={0}>{initials}</RAvatar.Fallback>
    </RAvatar.Root>
  );
}
