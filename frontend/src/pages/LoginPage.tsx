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
        <div className="auth-tabs" role="tablist" aria-label="Sign in or register">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "login"}
            className={`auth-tab${mode === "login" ? " active" : ""}`}
            onClick={() => setMode("login")}
          >
            Log in
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "register"}
            className={`auth-tab${mode === "register" ? " active" : ""}`}
            onClick={() => setMode("register")}
          >
            Register
          </button>
        </div>

        <div className="auth-brand">
          <span className="auth-brand-name">Interview Coach</span>
          <span className="auth-brand-mark">Personalized interview practice · runs on your machine</span>
        </div>

        <h1 className="auth-title">
          {mode === "login" ? (
            <>
              Welcome back.
              <br />
              Your packet is where you left it.
            </>
          ) : (
            "Practice the role in front of you: your CV, the job description, one topic at a time."
          )}
        </h1>
        <p className="auth-sub">
          Upload your CV and project docs, paste a job description, and a local model runs a real
          back-and-forth interview. It asks, probes, clarifies, then scores each topic and shows a
          model answer.
        </p>

        <ul className="auth-proof" aria-label="What makes it different">
          <li>
            <i aria-hidden="true" />
            Questions grounded in your CV, your project docs, and your own GitHub code.
          </li>
          <li>
            <i aria-hidden="true" />
            A real interviewer: one topic at a time, with follow-ups, then a score and a model answer.
          </li>
          <li>
            <i aria-hidden="true" />
            Private by construction: a local model on your GPU. Nothing leaves the box except optional
            web search.
          </li>
        </ul>

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
          <span>Local, self-hosted. Your documents stay on this machine.</span>
          <span>
            {mode === "login" ? "New here? Use the Register tab." : "Already registered? Use the Log in tab."}
          </span>
        </div>
      </div>
    </main>
  );
}
