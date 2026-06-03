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

  it("parses the Phase-34 action envelope (move + bare-int score + wrap)", () => {
    const frames = parseSseText(
      'event: move\ndata: {"kind": "probe", "thread_index": 0, "message_id": "m0"}\n\n' +
        "event: token\ndata: \"What tradeoff?\"\n\n" +
        "event: score\ndata: 7\n\n" +
        'event: evaluation_done\ndata: {"thread_index": 0, "session_status": "complete", "n_remaining": 0}\n\n' +
        'event: wrap\ndata: {"session_status": "complete"}\n\n',
    );

    expect(frames).toEqual([
      { event: "move", data: { kind: "probe", thread_index: 0, message_id: "m0" } },
      { event: "token", data: "What tradeoff?" },
      // Phase 34: the score is a bare integer on the wire, not {score: n}.
      { event: "score", data: 7 },
      { event: "evaluation_done", data: { thread_index: 0, session_status: "complete", n_remaining: 0 } },
      { event: "wrap", data: { session_status: "complete" } },
    ]);
  });
});
