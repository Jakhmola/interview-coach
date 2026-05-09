import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { SetupPage } from "./pages/SetupPage";
import { InterviewPage } from "./pages/InterviewPage";
import { HistoryPage } from "./pages/HistoryPage";
import { useAuth } from "./state/auth";

function Protected({ children }: { children: ReactNode }) {
  const { token, isBooting } = useAuth();
  if (isBooting) {
    return <div className="boot-screen">Opening the studio...</div>;
  }
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <Protected>
            <AppShell />
          </Protected>
        }
      >
        <Route index element={<Navigate to="/setup" replace />} />
        <Route path="setup" element={<SetupPage />} />
        <Route path="interview" element={<InterviewPage />} />
        <Route path="history" element={<HistoryPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/setup" replace />} />
    </Routes>
  );
}
