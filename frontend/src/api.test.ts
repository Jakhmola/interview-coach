import { describe, expect, it } from "vitest";

import { parseSseText } from "./api";

describe("parseSseText", () => {
  it("parses named JSON and string frames", () => {
    const frames = parseSseText(
      'event: score\ndata: {"score": 8}\n\nevent: feedback_token\ndata: "Strong start."\n\n',
    );

    expect(frames).toEqual([
      { event: "score", data: { score: 8 } },
      { event: "feedback_token", data: "Strong start." },
    ]);
  });

  it("keeps plain data as text", () => {
    expect(parseSseText("event: token\ndata: hello\n\n")).toEqual([
      { event: "token", data: "hello" },
    ]);
  });
});
