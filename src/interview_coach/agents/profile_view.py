"""Profile slicing for prompt trimming (Phase 20).

The full ``profile_json`` (typically 6-12 KB) is overkill for per-turn LLM
calls when the deterministic focus picker has already chosen which
highlight or project the question will drill into. This helper returns a
focus-anchored slice for those cases, and falls through to the full
profile when no useful anchor exists.

``focus_key`` is the same opaque token the picker stamps onto
``turn.metadata_json``:

* ``highlight:{exp_idx}:{hl_idx}`` — resume-walkthrough highlight.
* ``project:{name|idx_N}`` — resume-walkthrough project.
* free-form signal string — behavioral round; no anchor in the profile.
* ``None`` — picker found no candidates (degenerate profile).

Shape returned (mirrors :class:`Profile` field names so callers and the
LLM see the same vocabulary):

* anchored case: ``{summary, skills, focus, anchor_experience |
  anchor_project, other_experiences?}``
* unanchored / malformed-key case: the full profile dict, unchanged.
"""

from __future__ import annotations

from typing import Any


def profile_slice_for_focus(
    profile: dict[str, Any] | None,
    focus_key: str | None,
) -> dict[str, Any]:
    """Return a compact profile dict scoped to ``focus_key``.

    For behavioral / unknown / malformed focus keys, returns the full
    profile unchanged — losing context there is worse than the token
    cost, and behavioral-round trimming is its own problem (Phase 22+).
    """
    if not profile:
        return {}

    if focus_key and focus_key.startswith("highlight:"):
        sliced = _slice_highlight(profile, focus_key)
        return sliced if sliced is not None else profile
    if focus_key and focus_key.startswith("project:"):
        sliced = _slice_project(profile, focus_key)
        return sliced if sliced is not None else profile

    # Behavioral signal, None, or unknown key prefix — keep the full
    # profile. The LLM may need cross-experience signal that we can't
    # pre-identify without re-running the picker's scoring.
    return profile


def _slice_highlight(profile: dict[str, Any], focus_key: str) -> dict[str, Any] | None:
    """Return the slice for ``highlight:i:j``, or None if the key doesn't
    resolve (caller falls back to the full profile)."""
    try:
        _, exp_s, hl_s = focus_key.split(":", 2)
        exp_idx = int(exp_s)
        hl_idx = int(hl_s)
    except ValueError:
        return None

    experiences = profile.get("experiences") or []
    if not (0 <= exp_idx < len(experiences)):
        return None
    anchor_exp = experiences[exp_idx]
    if not isinstance(anchor_exp, dict):
        return None

    # Stubs for the other experiences so the LLM can still reference
    # cross-job context ("you built X at A, then scaled it at B") without
    # paying for the full enrichment payload.
    others = [
        {
            "company": e.get("company", ""),
            "role": e.get("role", ""),
            "start": e.get("start"),
            "end": e.get("end"),
        }
        for i, e in enumerate(experiences)
        if i != exp_idx and isinstance(e, dict)
    ]

    out: dict[str, Any] = {
        "summary": profile.get("summary", ""),
        "skills": list(profile.get("skills") or []),
        "focus": {"kind": "highlight", "experience_idx": exp_idx, "highlight_idx": hl_idx},
        "anchor_experience": anchor_exp,
    }
    if others:
        out["other_experiences"] = others
    return out


def _slice_project(profile: dict[str, Any], focus_key: str) -> dict[str, Any] | None:
    """Return the slice for ``project:<name|idx_N>``, or None if the key
    doesn't resolve."""
    name_or_idx = focus_key[len("project:") :]
    projects = profile.get("projects") or []
    chosen: dict[str, Any] | None = None
    chosen_idx: int | None = None

    if name_or_idx.startswith("idx_"):
        try:
            idx = int(name_or_idx[len("idx_") :])
        except ValueError:
            return None
        if 0 <= idx < len(projects) and isinstance(projects[idx], dict):
            chosen = projects[idx]
            chosen_idx = idx
    else:
        for i, p in enumerate(projects):
            if isinstance(p, dict) and (p.get("name") or "").strip() == name_or_idx:
                chosen = p
                chosen_idx = i
                break

    if chosen is None:
        return None

    return {
        "summary": profile.get("summary", ""),
        "skills": list(profile.get("skills") or []),
        "focus": {"kind": "project", "project_idx": chosen_idx},
        "anchor_project": chosen,
    }
