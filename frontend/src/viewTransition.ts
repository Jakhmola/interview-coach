import { flushSync } from "react-dom";

type Handle = { updateCallbackDone: Promise<void>; finished: Promise<void> };

const settled: Handle = { updateCallbackDone: Promise.resolve(), finished: Promise.resolve() };

type VTDocument = Document & {
  startViewTransition?: (update: () => void) => {
    updateCallbackDone: Promise<void>;
    finished: Promise<void>;
  };
};

/**
 * Apply a React state update inside a document view transition where the
 * browser has one (Chromium, Safari 18, Firefox 144+); elsewhere, and under
 * reduced motion, apply it plainly. `kind` sits on `<html data-vt>` for the
 * duration so the stylesheet can pick the choreography (e.g. "route").
 */
export function viewTransition(update: () => void, kind?: string): Handle {
  const doc = document as VTDocument;
  if (!doc.startViewTransition || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    update();
    return settled;
  }
  if (kind) doc.documentElement.dataset.vt = kind;
  const vt = doc.startViewTransition(() => flushSync(update));
  const finished = vt.finished
    .catch(() => {})
    .then(() => {
      if (kind) delete doc.documentElement.dataset.vt;
    });
  return { updateCallbackDone: vt.updateCallbackDone.catch(() => {}), finished };
}
