import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { can } from "./can";

describe("can", () => {
  it("Phase 1 stub always returns allowed:true", () => {
    assert.deepEqual(can({ id: "u1" }, "runs:read"), { allowed: true });
    assert.deepEqual(can({ id: "u1" }, "runs:write", { type: "run", id: "r1" }), {
      allowed: true,
    });
  });
});
