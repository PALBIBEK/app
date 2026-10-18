import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { t } from "./t";

describe("t", () => {
  it("returns catalog strings", () => {
    assert.equal(t("nav.catalog"), "Catalog");
    assert.equal(t("nav.runs"), "Runs");
  });

  it("interpolates variables", () => {
    assert.equal(
      t("error.permission", { missing: "admin" }),
      "You need admin permission for this action.",
    );
    assert.equal(
      t("error.budgetExceeded", { limit: "tokens", current: 42 }),
      "Budget tokens exceeded (current: 42).",
    );
  });

  it("leaves unknown placeholders intact", () => {
    assert.equal(t("error.notFound", {}), "{resource} was not found.");
  });
});
