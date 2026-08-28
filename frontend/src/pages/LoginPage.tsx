import { FormEvent, useState } from "react";
import { Navigate } from "react-router-dom";
import { ArrowRight } from "lucide-react";

import { ErrorBanner, Staples } from "../components/ui";
import { codeFrom } from "../errors";
import { useAuth } from "../state/auth";

type Mode = "login" | "register";

/** The packet's cover sheet: the pitch on the form, the sign-in as its fields. */
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
        <Staples />
        <div className="auth-tabs tabs" role="tablist" aria-label="Sign in or register">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "login"}
            className={`tab${mode === "login" ? " active" : ""}`}
            onClick={() => setMode("login")}
          >
            Log in
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "register"}
            className={`tab${mode === "register" ? " active" : ""}`}
            onClick={() => setMode("register")}
          >
            Register
          </button>
        </div>

        <div className="auth-brand">
          <span className="auth-brand-name">Interview Coach</span>
          <span className="auth-brand-mark">Personalized interview practice · runs on your machine</span>
        </div>

        <div className="auth-pitch">
          <h1 className="auth-title">Practice for the role in front of you.</h1>
          <p className="auth-sub">
            Add your CV, docs and repos. A local interviewer asks, probes, then scores each topic and
            shows a model answer.
          </p>
        </div>

        <form onSubmit={onSubmit} className="auth-form">
          <div className="auth-fields">
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
          </div>

          <ErrorBanner code={error} />

          <button className="btn-primary auth-submit" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Working…" : mode === "login" ? "Log in" : "Create account"}
            <ArrowRight />
          </button>
        </form>

        <div className="auth-foot">
          <button
            type="button"
            className="btn-quiet"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
          >
            {mode === "login" ? "New here? Create an account" : "Already registered? Log in"}
          </button>
        </div>
      </div>
    </main>
  );
}
