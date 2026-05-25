import { describe, expect, it } from "vitest";

import type { JobDetail } from "../../api";
import { shouldFetchActiveJobDetail } from "../activeJob";

// Phase 29 (D): `activeJob` detail is derived async state keyed on
// `activeJobId`. `shouldFetchActiveJobDetail` is the guard the
// detail-follows-id effect runs on — it fires a fetch exactly when the held
// detail lags the id, and no-ops once a matching detail lands (no loop, no
// re-fetch after resolve()/a prior fetch already matched). This table pins
// that contract.

function job(id: string): JobDetail {
  return {
    id,
    user_id: "u1",
    source: "pasted",
    char_count: 0,
    preview: "",
    created_at: "2026-01-01T00:00:00Z",
    raw_text: "",
  };
}

describe("shouldFetchActiveJobDetail", () => {
  it.each([
    // [name, activeJobId, activeJob, expected]
    ["no active id, no detail → idle", null, null, false],
    ["clearing: id null but a detail lingers → no fetch", null, job("A"), false],
    ["cold load: id set, detail not yet loaded → fetch", "A", null, true],
    ["matched: detail already follows the id → no-op", "A", job("A"), false],
    ["switched: id moved ahead of the held detail → fetch", "B", job("A"), true],
  ] as const)("%s", (_name, id, detail, expected) => {
    expect(shouldFetchActiveJobDetail(id, detail)).toBe(expected);
  });
});
