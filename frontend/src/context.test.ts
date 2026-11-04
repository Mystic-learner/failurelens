import { describe, expect, it } from "vitest";
import { splitContext, trimContext } from "./context";

describe("splitContext", () => {
  const chunk = "a start\nERROR boom happened\nINFO ok";

  it("marks only the line equal to the quote", () => {
    const lines = splitContext(chunk, "ERROR boom happened");
    expect(lines.map((l) => l.hit)).toEqual([false, true, false]);
  });

  it("does not highlight on a substring", () => {
    expect(splitContext(chunk, "boom").some((l) => l.hit)).toBe(false);
  });

  it("ignores surrounding whitespace and an empty quote", () => {
    expect(splitContext("  x line  \n", " x line ")[0]!.hit).toBe(true);
    expect(splitContext(chunk, "   ").some((l) => l.hit)).toBe(false);
  });
});

describe("trimContext", () => {
  const long = Array.from({ length: 40 }, (_, i) => `line ${i}`).join("\n");

  it("keeps a window around the hit and marks gaps", () => {
    const out = trimContext(splitContext(long, "line 20"), 2);
    expect(out.filter((l) => l.hit)).toHaveLength(1);
    expect(out.filter((l) => l.gap)).toHaveLength(2);
    expect(out.filter((l) => !l.gap)).toHaveLength(5);
  });

  it("truncates a long chunk with no hit", () => {
    const out = trimContext(splitContext(long, "nothing"), 4);
    expect(out).toHaveLength(9);
    expect(out.at(-1)!.gap).toBe(true);
  });

  it("returns short chunks unchanged", () => {
    const lines = splitContext("a\nb", "b");
    expect(trimContext(lines)).toEqual(lines);
  });
});