export type FieldError = {
  field: string;
  message: string;
};

export type AppError =
  | { kind: "network"; retryable: true }
  | { kind: "auth"; action: "reauthenticate" }
  | { kind: "permission"; missing: string }
  | { kind: "validation"; fields: FieldError[] }
  | { kind: "notFound"; resource: string }
  | { kind: "rateLimit"; retryAfter: number }
  | { kind: "server"; requestId?: string; retryable: true }
  | { kind: "budgetExceeded"; limit: string; current: number };

type ErrorBody = {
  kind?: string;
  missing?: string;
  fields?: FieldError[];
  resource?: string;
  retryAfter?: number;
  limit?: string;
  current?: number;
  message?: string;
  code?: string;
};

function asRecord(body: unknown): ErrorBody {
  if (body && typeof body === "object") {
    return body as ErrorBody;
  }
  return {};
}

/**
 * Map an HTTP status (+ optional JSON body) to a typed AppError.
 * Supervisor error shape is not finalized yet — this is the Phase 1 stub contract.
 */
export function mapHttpError(
  status: number,
  body: unknown,
  requestId?: string,
): AppError {
  const data = asRecord(body);

  if (data.kind === "budgetExceeded" || status === 402) {
    return {
      kind: "budgetExceeded",
      limit: typeof data.limit === "string" ? data.limit : "unknown",
      current: typeof data.current === "number" ? data.current : 0,
    };
  }

  if (status === 401) {
    return { kind: "auth", action: "reauthenticate" };
  }

  if (status === 403) {
    return {
      kind: "permission",
      missing: typeof data.missing === "string" ? data.missing : "unknown",
    };
  }

  if (status === 404) {
    return {
      kind: "notFound",
      resource: typeof data.resource === "string" ? data.resource : "unknown",
    };
  }

  if (status === 422 || status === 400) {
    return {
      kind: "validation",
      fields: Array.isArray(data.fields) ? data.fields : [],
    };
  }

  if (status === 429) {
    return {
      kind: "rateLimit",
      retryAfter: typeof data.retryAfter === "number" ? data.retryAfter : 0,
    };
  }

  if (status >= 500) {
    return requestId !== undefined
      ? { kind: "server", requestId, retryable: true }
      : { kind: "server", retryable: true };
  }

  return requestId !== undefined
    ? { kind: "server", requestId, retryable: true }
    : { kind: "server", retryable: true };
}
