import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiError, User, api } from "../api";

const TOKEN_KEY = "interview_coach.token";
const USER_KEY = "interview_coach.user";

type AuthContextValue = {
  token: string | null;
  user: User | null;
  isBooting: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function readStoredUser(): User | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as User;
  } catch {
    localStorage.removeItem(USER_KEY);
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState<User | null>(() => readStoredUser());
  const [isBooting, setIsBooting] = useState(Boolean(token));

  const persist = (nextToken: string, nextUser: User) => {
    localStorage.setItem(TOKEN_KEY, nextToken);
    localStorage.setItem(USER_KEY, JSON.stringify(nextUser));
    setToken(nextToken);
    setUser(nextUser);
  };

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  };

  useEffect(() => {
    if (!token) {
      setIsBooting(false);
      return;
    }
    let cancelled = false;
    api
      .me(token)
      .then((freshUser) => {
        if (!cancelled) {
          persist(token, freshUser);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled && error instanceof ApiError && error.status === 401) {
          logout();
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsBooting(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const value = useMemo<AuthContextValue>(
    () => ({
      token,
      user,
      isBooting,
      login: async (email, password) => {
        const response = await api.login(email.trim(), password);
        persist(response.access_token, response.user);
      },
      register: async (email, password) => {
        const response = await api.register(email.trim(), password);
        persist(response.access_token, response.user);
      },
      logout,
    }),
    [token, user, isBooting],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}
