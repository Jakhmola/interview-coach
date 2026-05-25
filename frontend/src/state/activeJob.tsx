import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, JobDetail, JobItem, api } from "../api";
import { useAuth } from "./auth";

const ACTIVE_JOB_KEY = "interview_coach.active_job_id";

type ActiveJobContextValue = {
  /** The currently-active job's id, or null. */
  activeJobId: string | null;
  /** Fully-fetched job detail when available. May lag behind activeJobId by one round-trip. */
  activeJob: JobDetail | null;
  /** All jobs for the current user, oldest-first per the backend.
   * Phase 22: lives on the context so a single ``refresh()`` call
   * keeps the sidebar dropdown, Setup wizard, Manage page, and
   * ReadyLanding all reading from one snapshot — no more "I switched
   * the active job but the dropdown still shows the old label". */
  jobs: JobItem[];
  /** Loading flag for the initial resolve + any refresh in flight. */
  isLoading: boolean;
  /** Set or clear the active job. Pass null to clear. */
  setActiveJobId: (id: string | null) => void;
  /** Re-fetch the active job detail AND the user's full job list. */
  refresh: () => Promise<void>;
};

const ActiveJobContext = createContext<ActiveJobContextValue | null>(null);

function readStoredId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_JOB_KEY);
  } catch {
    return null;
  }
}

function writeStoredId(id: string | null) {
  try {
    if (id === null) {
      localStorage.removeItem(ACTIVE_JOB_KEY);
    } else {
      localStorage.setItem(ACTIVE_JOB_KEY, id);
    }
  } catch {
    // ignore — quota or disabled storage; in-memory state still works
  }
}

/** True when the held detail lags the active id and a fetch should follow.
 * `activeJob` is derived async state keyed on `activeJobId`; this is the
 * guard that lets the detail-follows-id effect run exactly once per move and
 * no-op once the fetched detail matches (so it never loops or double-fetches
 * after resolve()/a fetch has already set a matching detail). */
export function shouldFetchActiveJobDetail(
  activeJobId: string | null,
  activeJob: JobDetail | null,
): boolean {
  return activeJobId !== null && activeJob?.id !== activeJobId;
}

export function ActiveJobProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const [activeJobId, setActiveJobIdState] = useState<string | null>(() => readStoredId());
  const [activeJob, setActiveJob] = useState<JobDetail | null>(null);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const setActiveJobId = useCallback((id: string | null) => {
    writeStoredId(id);
    setActiveJobIdState(id);
    if (id === null) {
      setActiveJob(null);
    }
  }, []);

  // On auth change, clear cached job detail so we don't show stale data
  // from a previous user.
  useEffect(() => {
    if (!token) {
      setActiveJob(null);
      setJobs([]);
    }
  }, [token]);

  /** Resolve the stored id against the server. Auto-fallback to most-recent JD.
   * Always also refreshes ``jobs`` so the sidebar dropdown stays in
   * sync with whatever the user just did (created/deleted/re-analyzed). */
  const resolve = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    try {
      // Always pull the list — it's cheap and it's the source of truth
      // every Phase 22 surface reads from.
      let nextJobs: JobItem[] = [];
      try {
        nextJobs = await api.listJobs(token);
        setJobs(nextJobs);
      } catch {
        // Best-effort; keep the previous list rather than blanking the UI.
      }

      const storedId = readStoredId();
      if (storedId) {
        try {
          const job = await api.getJob(token, storedId);
          setActiveJob(job);
          if (job.id !== activeJobId) {
            setActiveJobIdState(job.id);
          }
          return;
        } catch (err) {
          if (err instanceof ApiError && err.status === 404) {
            // Stale id — clear it silently and fall through to the fallback.
            writeStoredId(null);
          } else if (err instanceof ApiError && err.status === 401) {
            // Auth expired — surface; AuthProvider's storage listener handles it.
            throw err;
          } else {
            // Network blip etc. Don't nuke the id; just skip the fetch.
            return;
          }
        }
      }

      // Fallback: most recent JD from the list we just fetched.
      if (nextJobs.length === 0) {
        setActiveJobIdState(null);
        setActiveJob(null);
        return;
      }
      const newest = nextJobs[0];
      writeStoredId(newest.id);
      setActiveJobIdState(newest.id);
      try {
        const detail = await api.getJob(token, newest.id);
        setActiveJob(detail);
      } catch {
        // Best-effort; the list snapshot already drives the chip label.
      }
    } finally {
      setIsLoading(false);
    }
  }, [token, activeJobId]);

  // Resolve on mount + whenever the token changes (login/logout).
  useEffect(() => {
    void resolve();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Detail follows the id: when the active id moves ahead of the held detail
  // (e.g. a dropdown/Setup switch that only flips the id, no list refresh),
  // pull the matching JobDetail. Best-effort and silent — resolve() owns
  // isLoading, so this never flashes a second spinner; the list snapshot keeps
  // the chip label if the fetch fails. The `cancelled` flag drops a stale
  // result on unmount or a rapid re-switch (A→B→A settles on A).
  useEffect(() => {
    if (!token || activeJobId === null) return;
    if (!shouldFetchActiveJobDetail(activeJobId, activeJob)) return;
    let cancelled = false;
    void (async () => {
      try {
        const job = await api.getJob(token, activeJobId);
        if (!cancelled) setActiveJob(job);
      } catch {
        // Best-effort; keep the list-snapshot chip label.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, activeJobId, activeJob]);

  const value = useMemo<ActiveJobContextValue>(
    () => ({
      activeJobId,
      activeJob,
      jobs,
      isLoading,
      setActiveJobId,
      refresh: resolve,
    }),
    [activeJobId, activeJob, jobs, isLoading, setActiveJobId, resolve],
  );

  return <ActiveJobContext.Provider value={value}>{children}</ActiveJobContext.Provider>;
}

export function useActiveJob() {
  const v = useContext(ActiveJobContext);
  if (!v) {
    throw new Error("useActiveJob must be used inside ActiveJobProvider");
  }
  return v;
}

/** Re-export for type-side use without importing the api module everywhere. */
export type { JobDetail, JobItem };
