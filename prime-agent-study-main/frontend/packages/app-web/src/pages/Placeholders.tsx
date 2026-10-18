import { t } from "@yal/core";
import styles from "../shell.module.css";

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <div>
      <h1 style={{ fontFamily: "var(--font-display)", marginTop: 0 }}>{title}</h1>
      <p className={styles.placeholder}>
        Phase 1 placeholder — product content ships in Phase 2.
      </p>
    </div>
  );
}

export function CatalogPage() {
  return <PlaceholderPage title={t("nav.catalog")} />;
}
export function RunsPage() {
  return <PlaceholderPage title={t("nav.runs")} />;
}
export function ObservePage() {
  return <PlaceholderPage title={t("nav.observe")} />;
}
export function SettingsPage() {
  return <PlaceholderPage title={t("nav.settings")} />;
}
