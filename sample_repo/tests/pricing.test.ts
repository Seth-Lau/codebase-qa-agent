import { describe, expect, it } from "vitest";
import { calculateTotal } from "../lib/pricing";

describe("calculateTotal", () => {
  it("adds VAT", () => {
    expect(calculateTotal(1000, 1)).toBe(1200);
  });
  it("applies the bulk discount from 10 items", () => {
    expect(calculateTotal(1000, 10)).toBe(11400);
  });
});
