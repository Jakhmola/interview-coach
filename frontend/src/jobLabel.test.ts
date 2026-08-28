import { describe, expect, it } from "vitest";

import { excerptOf } from "./jobLabel";

describe("excerptOf", () => {
  it("quotes the head, keeps line breaks apart, and trails off when the text goes on", () => {
    expect(excerptOf("Priya Raman\nGenAI Engineer · Bengaluru\n\nExperience", 200)).toBe(
      "“Priya Raman / GenAI Engineer · Bengaluru / Experience…”",
    );
  });

  it("closes the quote plainly when the preview is the whole text", () => {
    expect(excerptOf("  Short  note ", 13)).toBe("“Short note”");
    expect(excerptOf("   ", 3)).toBe("");
  });
});
