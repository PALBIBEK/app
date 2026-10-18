import type { AppError } from "@yal/core";
import {
  Badge,
  Button,
  Checkbox,
  EmptyState,
  ErrorState,
  Input,
  Skeleton,
  Spinner,
  Switch,
} from "@yal/ui";
import { useState } from "react";
import styles from "../shell.module.css";

const errors: AppError[] = [
  { kind: "network", retryable: true },
  { kind: "auth", action: "reauthenticate" },
  { kind: "permission", missing: "runs.write" },
  { kind: "validation", fields: [{ field: "name", message: "Required" }] },
  { kind: "notFound", resource: "session" },
  { kind: "rateLimit", retryAfter: 30 },
  { kind: "server", requestId: "req_demo", retryable: true },
  { kind: "budgetExceeded", limit: "tokens", current: 120000 },
];

export function KitchenSinkPage() {
  const [density, setDensity] = useState(
    () => document.documentElement.getAttribute("data-density") ?? "comfortable",
  );

  return (
    <div className={styles.gallery}>
      <section>
        <h2>Kitchen sink</h2>
        <p className={styles.placeholder}>
          Dev-only gallery of primitives inside the real shell. Excluded from
          production builds via route tree.
        </p>
        <div className={styles.row}>
          <label className={styles.row}>
            Compact density
            <Switch
              checked={density === "compact"}
              onCheckedChange={(v) => {
                const next = v ? "compact" : "comfortable";
                setDensity(next);
                document.documentElement.setAttribute("data-density", next);
                localStorage.setItem("yal-density", next);
              }}
            />
          </label>
        </div>
      </section>

      <section>
        <h2>Actions</h2>
        <div className={styles.row}>
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button loading>Loading</Button>
          <Badge tone="success">success</Badge>
          <Badge tone="warning">warning</Badge>
          <Badge tone="danger">danger</Badge>
          <Spinner />
        </div>
      </section>

      <section>
        <h2>Inputs</h2>
        <div className={styles.row}>
          <Input label="Name" placeholder="coding-assistant" />
          <Checkbox>Select all</Checkbox>
        </div>
      </section>

      <section>
        <h2>Four-state gallery</h2>
        <div className={styles.states}>
          <div>
            <h3>Loading</h3>
            <Skeleton height={72} />
          </div>
          <EmptyState
            variant="no-data"
            title="No runs yet"
            body="Launch a run from the catalog to see it here."
            action={<Button>Open catalog</Button>}
          />
          <EmptyState
            variant="no-match"
            title="No matching runs"
            body="Nothing matches this filter. Clear filters to see all runs."
            action={<Button variant="secondary">Clear filters</Button>}
          />
          {errors.map((error) => (
            <ErrorState key={error.kind} error={error} onRetry={() => undefined} />
          ))}
          <div>
            <h3>Success</h3>
            <p className={styles.placeholder}>Session sess_demo settled · $0.61</p>
          </div>
        </div>
      </section>
    </div>
  );
}
