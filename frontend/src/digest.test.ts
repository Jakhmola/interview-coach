import { describe, expect, it } from "vitest";

import { currentRoleOf, filedUnder, leadOf, openingOf, rolesOf, sectionsOf, titleCase } from "./digest";

const DOC = `LLM-Powered Conversational Agent - Bobble AI (design note)
Synthetic project write-up for UI review. Architecture note for the assistant.
Retrieval
Product docs were chunked at 400-600 tokens with 15% overlap and embedded into pgvector.
Generation and prompts
Prompts were assembled from versioned blocks (tone, guidelines, output format).
Evaluation and guardrails
An offline set of 1,200 labelled prompts scored relevance and format compliance.`;

const CV = `Priya Raman
GenAI Engineer · Bengaluru · priya.raman@example.com
Experience
Bobble AI - Senior Machine Learning Engineer (2022 - present)
Designed a modular prompt system for a keyboard assistant.
Aurora Analytics - Data Scientist (2019 - 2022)
Skills
Python, PyTorch, LangChain
Education
M.Tech Computer Science, IIT Madras, 2019`;

const PROFILE = {
  experiences: [
    {
      company: "bobble ai",
      role: "senior machine learning engineer",
      start: "2022",
      end: "present",
      highlights: [{ text: "x", source_document_ids: ["doc-1"] }],
    },
    { company: "aurora analytics", role: "data scientist", start: "2019", end: "2022", highlights: [] },
  ],
};

describe("titleCase", () => {
  it("gives the profile builder's lowercase names their capitals back", () => {
    expect(titleCase("senior machine learning engineer, bobble ai")).toBe(
      "Senior Machine Learning Engineer, Bobble AI",
    );
    expect(titleCase("m.tech computer science")).toBe("M.tech Computer Science");
    expect(titleCase("iit madras")).toBe("IIT Madras");
    expect(titleCase("head of data")).toBe("Head of Data");
  });
});

describe("sectionsOf", () => {
  it("reads a document's headings and skips the title, sentences, bullets and dated lines", () => {
    expect(sectionsOf(DOC)).toEqual(["Retrieval", "Generation and prompts", "Evaluation and guardrails"]);
    expect(sectionsOf(CV)).toEqual(["Experience", "Skills", "Education"]);
  });
});

describe("leadOf", () => {
  it("keeps the first two sentences of a pitch", () => {
    expect(leadOf("I design systems. At Bobble AI, I built X. My work at Aurora included Y.")).toBe(
      "I design systems. At Bobble AI, I built X.",
    );
    expect(leadOf("One sentence only")).toBe("One sentence only");
  });
});

describe("openingOf", () => {
  it("is the first sentence of the first paragraph after the title", () => {
    expect(openingOf(DOC)).toBe("Synthetic project write-up for UI review.");
    expect(openingOf("Title\nShort line\n", 220)).toBe("Short line");
  });
});

describe("profile readers", () => {
  it("lists roles newest first and names the current one", () => {
    expect(rolesOf(PROFILE)).toEqual([
      "Senior Machine Learning Engineer, Bobble AI (2022 - present)",
      "Data Scientist, Aurora Analytics (2019 - 2022)",
    ]);
    expect(currentRoleOf(PROFILE)).toBe("Senior Machine Learning Engineer · Bobble AI");
    expect(currentRoleOf(null)).toBeNull();
  });

  it("finds the company a supporting doc was filed under", () => {
    expect(filedUnder(PROFILE, "doc-1")).toBe("Bobble AI");
    expect(filedUnder(PROFILE, "doc-2")).toBeNull();
  });
});
