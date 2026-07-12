import { type FormEvent, useState } from "react";

import { login, register, setAccessToken } from "../api/client";

type AuthPageProps = {
  onAuthenticated: () => void;
};

export function AuthPage({ onAuthenticated }: AuthPageProps) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      if (mode === "register") {
        await register(email, password);
      }
      const token = await login(email, password);
      setAccessToken(token.access_token);
      onAuthenticated();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Authentication failed");
    } finally {
      setLoading(false);
    }
  }

  function switchMode() {
    setMode((currentMode) => (currentMode === "login" ? "register" : "login"));
    setError(null);
  }

  return (
    <main className="auth-shell">
      <form className="auth-card" onSubmit={submit}>
        <div>
          <p className="eyebrow">Serverless Cloud Platform</p>
          <h1>{mode === "login" ? "Sign in" : "Create account"}</h1>
          <p>Use a JWT-backed account to manage functions and invocations.</p>
        </div>

        {error ? <div className="notice error">{error}</div> : null}

        <label>
          Email
          <input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            maxLength={320}
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            minLength={8}
            required
          />
        </label>
        <button className="primary-button" type="submit" disabled={loading}>
          {loading ? "Please wait" : mode === "login" ? "Sign in" : "Register"}
        </button>
        <button type="button" onClick={switchMode} disabled={loading}>
          {mode === "login" ? "Create an account" : "Use an existing account"}
        </button>
      </form>
    </main>
  );
}
