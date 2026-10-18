import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { formatCost } from "./cost";

describe("formatCost", () => {
  it("formats USD with explicit locale", () => {
    const result = formatCost(12.5, { locale: "en-US", currency: "USD" });
    assert.match(result, /\$12\.50/);
  });

  it("formats EUR with explicit locale", () => {
    const result = formatCost(3, { locale: "en-IE", currency: "EUR" });
    assert.match(result, /€3\.00|EUR\s?3\.00/);
  });
});
