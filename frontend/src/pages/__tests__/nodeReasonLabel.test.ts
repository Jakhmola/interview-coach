import { describe, expect, it } from "vitest";

import type { PrepRunReason, PrepSkipReason } from "../../api";
import { nodeReasonLabel } from "../SetupPage";

// Phase 28: nodeReasonLabel is total over (node, reason, pill). It returns a
// terminal sub-label only for the three run reasons that tell a story, and null
// for everything else (missing, all skip reasons, impossible combos, conflicts)
// so TaskStatus keeps its plain fallback. These tables assert that contract.

describe("nodeReasonLabel — story reasons that render", () => {
  it.each([
    ["profile_builder", "stale", "done", "Rebuilt — your documents changed"],
    [
      "company_researcher",
      "degraded",
      "done",
      "Recovered — earlier company info was incomplete",
    ],
  ] as const)("%s × %s × %s → copy", (node, reason, pill, expected) => {
    expect(nodeReasonLabel(node, reason, pill)).toBe(expected);
  });
});

describe("nodeReasonLabel — outcome wins on conflict (company degraded)", () => {
  it("degraded run that settled degraded defers to the warning/toast (null)", () => {
    // The settled pill is `degraded` (node_done outcome === "degraded"), so the
    // reason adds nothing — the existing Phase-27 warning + toast speak instead.
    expect(nodeReasonLabel("company_researcher", "degraded", "degraded")).toBeNull();
  });
});

describe("nodeReasonLabel — no-story reasons return null", () => {
  const allNodes = [
    "profile_builder",
    "doc_mapping",
    "job_analyzer",
    "company_researcher",
  ] as const;
  const skipReasons: PrepSkipReason[] = [
    "cached",
    "already_analyzed",
    "no_unmapped_project_docs",
  ];

  it.each(allNodes)("`missing` on %s is silent (fresh-setup happy path)", (node) => {
    expect(nodeReasonLabel(node, "missing", "done")).toBeNull();
  });

  it.each(skipReasons)("skip reason `%s` keeps today's copy on every node", (reason) => {
    for (const node of allNodes) {
      expect(nodeReasonLabel(node, reason, "cached")).toBeNull();
      expect(nodeReasonLabel(node, reason, "done")).toBeNull();
    }
  });

  it("undefined reason (no reason on the wire) returns null", () => {
    expect(nodeReasonLabel("profile_builder", undefined, "done")).toBeNull();
    expect(nodeReasonLabel("company_researcher", undefined, "done")).toBeNull();
  });
});

describe("nodeReasonLabel — impossible / mismatched combos degrade to null", () => {
  it.each([
    // `forced` has no UI trigger (the Refresh company info button is gone), so
    // it is deliberately unhandled even on the node prep_cache.py fires it on.
    ["company_researcher", "forced", "done"],
    // Story reason on a node where prep_cache.py never fires it.
    ["company_researcher", "stale", "done"],
    ["profile_builder", "degraded", "done"],
    ["job_analyzer", "stale", "done"],
    // Right node+reason but a non-terminal/wrong pill — only `done` renders.
    ["profile_builder", "stale", "running"],
    ["profile_builder", "stale", "pending"],
    ["company_researcher", "degraded", "running"],
  ] as const)("%s × %s × %s → null", (node, reason, pill) => {
    expect(nodeReasonLabel(node, reason as PrepRunReason, pill)).toBeNull();
  });
});
