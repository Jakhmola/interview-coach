import { describe, expect, it } from "vitest";

import { verdict } from "./jobLabel";

const t = (i: number, score: number | null) => ({ thread_index: i, score });

describe("verdict", () => {
  it("is silent until something is scored", () => {
    expect(verdict([])).toBeNull();
    expect(verdict([t(0, null)])).toBeNull();
  });

  it("bands the average", () => {
    expect(verdict([t(0, 8)])).toBe("Strong round.");
    expect(verdict([t(0, 6)])).toBe("Solid round.");
    expect(verdict([t(0, 5)])).toBe("Keep at it.");
  });

  it("names the strongest and weakest topic when they differ", () => {
    expect(verdict([t(0, 8), t(1, 6), t(2, 5)])).toBe("Solid round. Strongest on topic 1, weakest on topic 3.");
    expect(verdict([t(0, 7), t(1, null), t(2, 9)])).toBe("Strong round. Strongest on topic 3, weakest on topic 1.");
  });

  it("stays with the band when every topic scored the same", () => {
    expect(verdict([t(0, 6), t(1, 6)])).toBe("Solid round.");
  });
});
