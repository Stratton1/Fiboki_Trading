"use client";

import type { QueryClient } from "@tanstack/react-query";
import { apiFetch, LOGIN_PATH } from "./api";
import { resetLive } from "./live-store";
import { apiKey } from "./query";
import type { PrincipalView } from "./types";

/**
 * Sign in, sign out, and what the signed-in operator may do.
 *
 * The session is the backend's httpOnly cookie; the workstation never sees a
 * credential after it has been posted, never puts one in a URL, and never
 * stores one. The principal (GET /api/auth/me) is only ever displayed and used
 * to disable controls the platform would refuse anyway: the server's RBAC is
 * the authority, this is the courtesy of saying so before the click.
 */

export const ME_PATH = "/api/auth/me";

export async function signIn(
  client: QueryClient,
  username: string,
  password: string,
): Promise<PrincipalView> {
  const principal = await apiFetch<PrincipalView>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  // Everything cached was read without this session; read it again.
  client.clear();
  client.setQueryData(apiKey(ME_PATH), principal);
  return principal;
}

export async function signOut(client: QueryClient): Promise<void> {
  await apiFetch<void>("/api/auth/logout", { method: "POST" });
  client.clear();
  resetLive();
  window.location.href = LOGIN_PATH;
}

export type Capability = "can_arm_kill_switch" | "can_promote" | "can_acknowledge";

const WHAT: Record<Capability, string> = {
  can_arm_kill_switch: "arm or disarm the kill switch",
  can_promote: "promote a candidate",
  can_acknowledge: "acknowledge an incident",
};

/**
 * Whether the signed-in operator may use a control, and if not, why.
 *
 * An unknown principal (the platform did not answer /api/auth/me) does not
 * disable anything: the server still refuses what the role cannot do, and a
 * kill switch must never be blocked by the workstation's own uncertainty.
 * `can_acknowledge` has no flag of its own on PrincipalView; the backend's
 * acknowledge route requires the admin role (AdminPrincipal in
 * routers/incidents.py), so only an admin may. A `can_acknowledge` flag on
 * PrincipalView would remove this duplication of a backend rule.
 */
export function capability(
  principal: PrincipalView | null,
  cap: Capability,
): { allowed: boolean; reason: string | null } {
  if (principal === null) return { allowed: true, reason: null };
  const allowed =
    cap === "can_acknowledge" ? principal.role === "admin" : principal[cap] === true;
  if (allowed) return { allowed: true, reason: null };
  return {
    allowed: false,
    reason: `Signed in as ${principal.display_name} (${principal.role}). This role cannot ${WHAT[cap]}; the platform would refuse it.`,
  };
}
