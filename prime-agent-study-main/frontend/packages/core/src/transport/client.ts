import { mapHttpError, type AppError } from "./errors";

const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_RETRIES = 2;
const BASE_BACKOFF_MS = 250;

export class TransportError extends Error {
  readonly error: AppError;

  constructor(error: AppError) {
    super(`transport:${error.kind}`);
    this.name = "TransportError";
    this.error = error;
  }
}

export type RequestOptions = RequestInit & {
  timeoutMs?: number;
  retries?: number;
};

function createRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function isRetryableStatus(status: number): boolean {
  return status === 429 || status >= 500;
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

/**
 * Same-origin fetch wrapper: credentials, request id, timeout, retry backoff,
 * and AppError mapping. Generated OpenAPI clients will sit underneath this later.
 */
export async function request<T>(path: string, init: RequestOptions = {}): Promise<T> {
  const {
    timeoutMs = DEFAULT_TIMEOUT_MS,
    retries = MAX_RETRIES,
    headers: initHeaders,
    signal: outerSignal,
    ...rest
  } = init;

  const requestId = createRequestId();
  let attempt = 0;
  let lastError: AppError | undefined;

  while (attempt <= retries) {
    const controller = new AbortController();
    const timeout = setTimeout(() => {
      controller.abort();
    }, timeoutMs);

    const onAbort = () => {
      controller.abort();
    };
    outerSignal?.addEventListener("abort", onAbort);

    try {
      const headers = new Headers(initHeaders);
      if (!headers.has("X-Request-Id")) {
        headers.set("X-Request-Id", requestId);
      }
      if (!headers.has("Accept")) {
        headers.set("Accept", "application/json");
      }

      const response = await fetch(path, {
        ...rest,
        credentials: "include",
        headers,
        signal: controller.signal,
      });

      if (!response.ok) {
        const body = await parseBody(response);
        const mapped = mapHttpError(
          response.status,
          body,
          response.headers.get("x-request-id") ?? requestId,
        );
        if (attempt < retries && isRetryableStatus(response.status)) {
          lastError = mapped;
          attempt += 1;
          await sleep(BASE_BACKOFF_MS * 2 ** (attempt - 1));
          continue;
        }
        throw new TransportError(mapped);
      }

      if (response.status === 204) {
        return undefined as T;
      }

      const body = await parseBody(response);
      return body as T;
    } catch (err) {
      if (err instanceof TransportError) {
        throw err;
      }
      const networkError: AppError = { kind: "network", retryable: true };
      if (attempt < retries) {
        lastError = networkError;
        attempt += 1;
        await sleep(BASE_BACKOFF_MS * 2 ** (attempt - 1));
        continue;
      }
      throw new TransportError(lastError ?? networkError);
    } finally {
      clearTimeout(timeout);
      outerSignal?.removeEventListener("abort", onAbort);
    }
  }

  throw new TransportError(lastError ?? { kind: "network", retryable: true });
}
