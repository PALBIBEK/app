import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { mapHttpError } from "./errors";

describe("mapHttpError", () => {
  it("maps 401 to auth", () => {
    assert.deepEqual(mapHttpError(401, {}), {
      kind: "auth",
      action: "reauthenticate",
    });
  });

  it("maps 403 to permission", () => {
    assert.deepEqual(mapHttpError(403, { missing: "runs:write" }), {
      kind: "permission",
      missing: "runs:write",
    });
  });

  it("maps 404 to notFound", () => {
    assert.deepEqual(mapHttpError(404, { resource: "run" }), {
      kind: "notFound",
      resource: "run",
    });
  });

  it("maps 422 to validation", () => {
    const fields = [{ field: "name", message: "required" }];
    assert.deepEqual(mapHttpError(422, { fields }), {
      kind: "validation",
      fields,
    });
  });

  it("maps 429 to rateLimit", () => {
    assert.deepEqual(mapHttpError(429, { retryAfter: 12 }), {
      kind: "rateLimit",
      retryAfter: 12,
    });
  });

  it("maps 500 to server with requestId", () => {
    assert.deepEqual(mapHttpError(500, {}, "req_abc"), {
      kind: "server",
      requestId: "req_abc",
      retryable: true,
    });
  });

  it("maps budgetExceeded body and 402", () => {
    assert.deepEqual(
      mapHttpError(402, { kind: "budgetExceeded", limit: "tokens", current: 100 }),
      { kind: "budgetExceeded", limit: "tokens", current: 100 },
    );
  });
});
