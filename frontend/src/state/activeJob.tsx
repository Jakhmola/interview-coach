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
  /** Loading flag for the initial resolve + any refresh in flight. */
  isLoading: boolean;
  /** Set or clear the active job. Pass null to clear. */
  setActiveJobId: (id: string | null) => void;
  /** Re-fetch the active job detail from the server (e.g. after a JD-analyze run). */
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

export function ActiveJobProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const [activeJobId, setActiveJobIdState] = useState<string | null>(() => readStoredId());
  const [activeJob, setActiveJob] = useState<JobDetail | null>(null);
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
    }
  }, [token]);

  /** Resolve the stored id against the server. Auto-fallback to most-recent JD. */
  const resolve = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    try {
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

      // Fallback: most recent JD (listJobs orders desc by created_at).
      try {
        const jobs = await api.listJobs(token);
        if (jobs.length === 0) {
          setActiveJobIdState(null);
          setActiveJob(null);
          return;
        }
        const newest = jobs[0];
        writeStoredId(newest.id);
        setActiveJobIdState(newest.id);
        const detail = await api.getJob(token, newest.id);
        setActiveJob(detail);
      } catch {
        // Best-effort.
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

  const value = useMemo<ActiveJobContextValue>(
    () => ({
      activeJobId,
      activeJob,
      isLoading,
      setActiveJobId,
      refresh: resolve,
    }),
    [activeJobId, activeJob, isLoading, setActiveJobId, resolve],
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
