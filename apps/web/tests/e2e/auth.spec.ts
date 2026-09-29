import { expect, test, type Page, type Route } from "@playwright/test";
import { API, candidatesPage, mockShell, principal } from "./fixtures";
import { MockStream } from "./sse";

/**
 * auth.spec (gate for Wave 2, report E §8.1): sign in, sign out, the 401
 * redirect with the return path, the principal in the shell, and role gating
 * (a viewer sees admin actions disabled, with the reason, before clicking).
 */

const BASE = `http://127.0.0.1:${process.env.PORT ?? 3100}`;

/** Every URL the page navigated to, to prove no credential ever reached one. */
function recordNavigations(page: Page): string[] {
  const urls: string[] = [];
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) urls.push(frame.url());
  });
  return urls;
}

test.describe("sign in", () => {
  test("posts the credentials once, as JSON, and returns to the requested page", async ({ page }) => {
    await mockShell(page);
    const urls = recordNavigations(page);
    const posted: { url: string; body: unknown; method: string }[] = [];
    await page.route(`${API}/api/auth/login`, async (route: Route) => {
      const request = route.request();
      posted.push({ url: request.url(), body: request.postDataJSON(), method: request.method() });
      await route.fulfill({ json: principal({ user_id: "joe", display_name: "joe" }) });
    });

    await page.goto(`/login?next=${encodeURIComponent("/trading/risk")}`);
    await expect(page.getByTestId("login-form")).toBeVisible();
    await page.getByTestId("login-username").fill("joe");
    await page.getByTestId("login-password").fill("correct horse battery");
    await page.getByTestId("login-submit").click();

    await expect(page).toHaveURL(`${BASE}/trading/risk`);
    await expect(page.getByTestId("status-operator")).toContainText("joe (admin)");
    expect(posted).toEqual([
      {
        url: `${API}/api/auth/login`,
        method: "POST",
        body: { username: "joe", password: "correct horse battery" },
      },
    ]);
    for (const url of [...urls, ...posted.map((p) => p.url)]) {
      expect(url).not.toContain("correct");
      expect(url).not.toContain("password");
    }
  });

  test("the form never pre-fills or autofills a secret, and has no shell chrome", async ({ page }) => {
    await mockShell(page);
    await page.goto("/login");
    const password = page.getByTestId("login-password");
    await expect(password).toHaveValue("");
    await expect(password).toHaveAttribute("type", "password");
    await expect(password).toHaveAttribute("autocomplete", "off");
    await expect(page.getByTestId("login-username")).toHaveValue("");
    await expect(page.getByTestId("login-form")).toHaveAttribute("method", "post");
    await expect(page.getByTestId("login-submit")).toBeDisabled();
    // The mode is public and shown; the workstation chrome is not.
    await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", "paper");
    await expect(page.getByTestId("status-bar")).toHaveCount(0);
    await expect(page.getByTestId("primary-nav")).toHaveCount(0);
  });

  test("a wrong password says so, clears the field and stays put", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/auth/login`, (route: Route) =>
      route.fulfill({
        status: 401,
        json: {
          code: "invalid_credentials",
          detail: "That username and password did not match.",
          correlation_id: "cid-login",
          context: {},
        },
      }),
    );
    await page.goto("/login?next=%2Ftrading%2Frisk");
    await page.getByTestId("login-username").fill("joe");
    await page.getByTestId("login-password").fill("wrong");
    await page.getByTestId("login-submit").click();
    const error = page.getByTestId("login-error");
    await expect(error).toContainText("did not match");
    await expect(error).toContainText("invalid_credentials");
    await expect(page.getByTestId("login-password")).toHaveValue("");
    await expect(page).toHaveURL(/\/login\?next=%2Ftrading%2Frisk$/);
  });

  test("an unreachable API is a connection failure, not a wrong password", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/auth/login`, (route: Route) => route.abort("failed"));
    await page.goto("/login");
    await page.getByTestId("login-username").fill("joe");
    await page.getByTestId("login-password").fill("anything");
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("login-error")).toContainText("not a wrong password");
  });

  test("a return path that leaves the workstation is refused", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/auth/login`, (route: Route) => route.fulfill({ json: principal() }));
    await page.goto(`/login?next=${encodeURIComponent("//evil.example/steal")}`);
    await page.getByTestId("login-username").fill("joe");
    await page.getByTestId("login-password").fill("correct horse battery");
    await page.getByTestId("login-submit").click();
    await expect(page).toHaveURL(`${BASE}/`);
  });
});

test.describe("session", () => {
  test("a 401 sends the operator to sign in, keeping the return path", async ({ page }) => {
    await mockShell(page, { operator: "unauthenticated" });
    await page.goto("/trading/execution?provenance=paper");
    await expect(page).toHaveURL(
      `${BASE}/login?next=${encodeURIComponent("/trading/execution?provenance=paper")}`,
    );
    await expect(page.getByTestId("login-form")).toBeVisible();
  });

  test("a stream ended by an expired session sends the operator to sign in", async ({ page }) => {
    await mockShell(page);
    let expired = false;
    let meReads = 0;
    await page.route(`${API}/api/auth/me`, (route: Route) => {
      meReads += 1;
      return expired
        ? route.fulfill({
            status: 401,
            json: { code: "not_authenticated", detail: "Sign in to continue.", correlation_id: "c", context: {} },
          })
        : route.fulfill({ json: principal() });
    });
    const stream = new MockStream();
    await stream.install(page);
    await page.goto("/trading/risk");
    await expect(page.getByTestId("status-stream")).toHaveAttribute("data-state", "live");
    await expect(page.getByTestId("status-operator")).toBeVisible();
    const readsBefore = meReads;

    // The session expires. Nothing on this page polls "who am I"; only the
    // stream notices, as a refused reconnect (a 401 an EventSource cannot
    // see). The workstation re-asks who it is, and that 401 redirects.
    expired = true;
    stream.down = 401;
    await expect(page).toHaveURL(`${BASE}/login?next=${encodeURIComponent("/trading/risk")}`);
    expect(meReads).toBeGreaterThan(readsBefore);
  });

  test("the principal is in the status bar; sign-out posts with CSRF and lands on sign-in", async ({
    page,
    context,
  }) => {
    await context.addCookies([{ name: "fiboki_csrf", value: "csrf-token-123", url: BASE }]);
    await mockShell(page);
    const logouts: Record<string, string>[] = [];
    await page.route(`${API}/api/auth/logout`, async (route: Route) => {
      logouts.push(route.request().headers());
      await route.fulfill({ status: 204, body: "" });
    });
    await page.goto("/trading/risk");
    await expect(page.getByTestId("status-operator")).toContainText("Joe (admin)");
    await page.getByTestId("sign-out").click();
    await expect(page).toHaveURL(`${BASE}/login`);
    expect(logouts).toHaveLength(1);
    expect(logouts[0]?.["x-fiboki-csrf"]).toBe("csrf-token-123");
  });
});

test.describe("role gating", () => {
  const viewer = {
    user_id: "viv",
    display_name: "Viv",
    role: "viewer",
    can_arm_kill_switch: false,
    can_promote: false,
  };

  test("a viewer sees the kill switch disabled, with the reason", async ({ page }) => {
    await mockShell(page, { operator: viewer });
    await page.goto("/trading/risk");
    await expect(page.getByTestId("kill-switch-arm")).toBeDisabled();
    const reason = page.getByTestId("kill-switch-role-blocked");
    await expect(reason).toContainText("Viv (viewer)");
    await expect(reason).toContainText("cannot arm or disarm the kill switch");
  });

  test("a viewer sees promotion disabled, with the reason", async ({ page }) => {
    await mockShell(page, { operator: viewer });
    await page.route(`${API}/api/trading/candidates`, (route: Route) =>
      route.fulfill({ json: candidatesPage() }),
    );
    await page.goto("/trading/candidates");
    await expect(page.getByTestId("promote-ichimoku_kumo_trend")).toBeDisabled();
    await expect(page.getByTestId("promote-role-blocked")).toContainText("cannot promote a candidate");
  });

  test("a viewer, or an operator, cannot acknowledge an incident (admin only)", async ({ page }) => {
    await mockShell(page, { operator: viewer });
    await page.goto("/");
    await expect(page.getByTestId("incident-ack-inc-1")).toBeDisabled();
    await expect(page.getByTestId("incident-role-blocked")).toContainText("cannot acknowledge");
  });

  test("an operator-role principal also cannot acknowledge", async ({ page }) => {
    await mockShell(page, {
      operator: { role: "operator", display_name: "Olly", can_arm_kill_switch: false, can_promote: false },
    });
    await page.goto("/");
    await expect(page.getByTestId("incident-ack-inc-1")).toBeDisabled();
    await expect(page.getByTestId("incident-role-blocked")).toContainText("Olly (operator)");
  });

  test("an admin's controls are enabled", async ({ page }) => {
    await mockShell(page);
    await page.goto("/trading/risk");
    await expect(page.getByTestId("kill-switch-arm")).toBeEnabled();
    await expect(page.getByTestId("kill-switch-role-blocked")).toHaveCount(0);
  });

  test("an unknown principal disables nothing: the server decides", async ({ page }) => {
    await mockShell(page, { operator: false });
    await page.goto("/trading/risk");
    await expect(page.getByTestId("kill-switch-arm")).toBeEnabled();
    await expect(page.getByTestId("status-operator")).toHaveCount(0);
  });
});
