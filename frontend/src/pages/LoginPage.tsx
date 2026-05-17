import { FormEvent, useState } from "react";
import { Navigate } from "react-router-dom";
import { ArrowRight, Sparkles } from "lucide-react";

import { ErrorBanner } from "../components/ui";
import { codeFrom } from "../errors";
import { useAuth } from "../state/auth";

type Mode = "login" | "register";

export function LoginPage() {
  const { token, login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (token) {
    return <Navigate to="/setup" replace />;
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password);
      }
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="auth-brand-mark">
            <Sparkles size={18} />
          </span>
          <span className="auth-brand-name">Interview Coach</span>
        </div>

        <h1 className="auth-title">
          {mode === "login" ? "Welcome back." : "Make an account."}
        </h1>
        <p className="auth-sub">
          {mode === "login"
            ? "Pick up where you left off."
            : "Practice the role in front of you — your CV, the JD, one question at a time."}
        </p>

        <div className="auth-tabs">
          <button
            type="button"
            className={`auth-tab${mode === "login" ? " active" : ""}`}
            onClick={() => setMode("login")}
          >
            Log in
          </button>
          <button
            type="button"
            className={`auth-tab${mode === "register" ? " active" : ""}`}
            onClick={() => setMode("register")}
          >
            Register
          </button>
        </div>

        <form onSubmit={onSubmit} className="auth-form">
          <label className="auth-field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
              placeholder="you@example.com"
            />
          </label>
          <label className="auth-field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={mode === "register" ? 8 : undefined}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              required
              placeholder={mode === "register" ? "At least 8 characters" : ""}
            />
          </label>

          <ErrorBanner code={error} />

          <button className="btn-primary auth-submit" type="submit" disabled={isSubmitting}>
            {isSubmitting
              ? "Working…"
              : mode === "login"
                ? "Log in"
                : "Create account"}
            <ArrowRight size={14} />
          </button>
        </form>
      </div>
    </main>
  );
}
