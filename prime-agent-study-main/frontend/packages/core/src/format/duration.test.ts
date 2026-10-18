import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { formatDuration } from "./duration";

describe("formatDuration", () => {
  it("formats milliseconds under one second", () => {
    assert.equal(formatDuration(500), "500ms");
  });

  it("formats minutes and seconds", () => {
    assert.equal(formatDuration(84_000), "1m 24s");
  });

  it("formats hours and minutes", () => {
    assert.equal(formatDuration(7_380_000), "2h 3m");
  });

  it("handles zero and negative", () => {
    assert.equal(formatDuration(0), "0ms");
    assert.equal(formatDuration(-1500), "-1s");
  });
});
