"use client";

import { useQueryClient } from "@tanstack/react-query";
import { LogIn } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { PageHead } from "@/components/primitives";
import { Button } from "@/components/ui/Button";
import { ApiError, NEXT_PARAM, safeReturnPath } from "@/lib/api";
import { signIn } from "@/lib/auth";

/**
 * Sign in.
 *
 * Username and password are posted once, as JSON, to POST /api/auth/login;
 * the session comes back as an httpOnly cookie the workstation never reads.
 * The credentials are never put in a URL (the form is method="post" even
 * without JavaScript), never pre-filled, never stored, and the password field
 * is cleared after every attempt and has browser autofill switched off.
 *
 * The return path comes from `?next=` and is accepted only as an in-app path,
 * so this page cannot be used to bounce an operator to another site.
 */

function describe(error: unknown): { text: string; code: string } {
  if (!(error instanceof ApiError)) return { text: "The sign-in request failed.", code: "unexpected_error" };
  if (error.status === 0) {
    return {
      text: "Could not reach the Fiboki API to sign in. This is a connection failure, not a wrong password.",
      code: error.code,
    };
  }
  return { text: error.message, code: error.code };
}

export default function LoginPage() {
  const client = useQueryClient();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; code: string } | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await signIn(client, username.trim(), password);
      setPassword("");
      const next = safeReturnPath(new URLSearchParams(window.location.search).get(NEXT_PARAM));
      router.replace(next);
    } catch (err) {
      setPassword("");
      setError(describe(err));
      setBusy(false);
    }
  }

  return (
    <div className="login" data-testid="login">
      <PageHead
        title="Sign in"
        intro="Sign in with your operator account. The mode banner above shows where orders would go on this platform."
      />
      <form method="post" onSubmit={onSubmit} noValidate data-testid="login-form">
        <label htmlFor="login-username">Username</label>
        <input
          id="login-username"
          name="username"
          type="text"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          required
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          data-testid="login-username"
        />
        <label htmlFor="login-password">Password</label>
        <input
          id="login-password"
          name="password"
          type="password"
          autoComplete="off"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          data-testid="login-password"
        />
        {error ? (
          <p className="state state--error mt-2" role="alert" data-testid="login-error">
            {error.text} <span className="mono">({error.code})</span>
          </p>
        ) : null}
        <Button
          type="submit"
          variant="primary"
          className="mt-3"
          disabled={busy || username.trim() === "" || password === ""}
          aria-busy={busy}
          data-testid="login-submit"
        >
          <LogIn size={14} aria-hidden="true" />
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </div>
  );
}
