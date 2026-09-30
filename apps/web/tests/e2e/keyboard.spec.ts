import { expect, test, type Route } from "@playwright/test";
import { API, killSwitchView, mockShell, principal } from "./fixtures";

/**
 * Keyboard: ⌘⇧D cycles density, ⌘⇧L toggles the theme (Ctrl on Windows and
 * Linux); neither changes anything on the platform. Wave 3: ⌘K opens the
 * command palette, `g` then a letter goes to a screen, `?` lists every
 * shortcut, and ⇧K OPENS the kill-switch dialog, never arms it. Dialogs trap focus, hide
 * the page behind them from assistive technology, and give focus back to the
 * control that opened them. Tooltips open on keyboard focus and on tap.
 */

test.use({ viewport: { width: 1280, height: 800 } });

test.beforeEach(async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "keyboard model, desktop project");
  await mockShell(page);
});

test.describe("display shortcuts", () => {
  for (const modifier of ["Meta", "Control"]) {
    test(`${modifier}+Shift+D cycles density, remembered across a reload`, async ({ page }) => {
      await page.goto("/");
      const html = page.locator("html");
      await expect(html).toHaveAttribute("data-density", "regular");
      await page.keyboard.press(`${modifier}+Shift+KeyD`);
      await expect(html).toHaveAttribute("data-density", "comfortable");
      await expect(page.getByTestId("toast")).toContainText("Density: comfortable");
      await page.keyboard.press(`${modifier}+Shift+KeyD`);
      await expect(html).toHaveAttribute("data-density", "compact");
      await page.reload();
      await expect(html).toHaveAttribute("data-density", "compact");
      await page.keyboard.press(`${modifier}+Shift+KeyD`);
      await expect(html).toHaveAttribute("data-density", "regular");
    });

    test(`${modifier}+Shift+L toggles the theme, remembered across a reload`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: "dark" });
      await page.goto("/");
      const html = page.locator("html");
      await expect(html).toHaveAttribute("data-theme", "dark");
      const darkCanvas = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      await page.keyboard.press(`${modifier}+Shift+KeyL`);
      await expect(html).toHaveAttribute("data-theme", "light");
      await expect(page.getByTestId("toast")).toContainText("Theme: light");
      const lightCanvas = await page.evaluate(
        () => getComputedStyle(document.body).backgroundColor,
      );
      expect(lightCanvas).not.toBe(darkCanvas);
      await page.reload();
      await expect(html).toHaveAttribute("data-theme", "light");
      await page.keyboard.press(`${modifier}+Shift+KeyL`);
      await expect(html).toHaveAttribute("data-theme", "dark");
    });
  }

  test("the system theme is followed until the operator chooses", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await expect(page.locator("html")).toHaveAttribute("data-theme-pref", "system");
    await page.emulateMedia({ colorScheme: "dark" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });

  test("shortcuts post nothing to the platform", async ({ page }) => {
    const mutations: string[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET") mutations.push(`${request.method()} ${request.url()}`);
    });
    await page.goto("/trading/risk");
    const html = page.locator("html");
    const theme = await html.getAttribute("data-theme");
    const density = await html.getAttribute("data-density");
    await page.keyboard.press("ControlOrMeta+Shift+KeyD");
    await page.keyboard.press("ControlOrMeta+Shift+KeyL");
    await expect(html).not.toHaveAttribute("data-theme", theme ?? "");
    await expect(html).not.toHaveAttribute("data-density", density ?? "");
    expect(mutations).toEqual([]);
  });

  test("the display popover changes the P&L palette", async ({ page }) => {
    const pnlUp = () =>
      page.evaluate(() => {
        const probe = document.createElement("div");
        probe.style.color = "var(--pnl-up)";
        document.body.append(probe);
        const colour = getComputedStyle(probe).color;
        probe.remove();
        return colour;
      });
    await page.goto("/");
    const standard = await pnlUp();
    await page.getByTestId("display-settings-trigger").click();
    await page.getByTestId("pref-pnl").getByRole("radio", { name: "Blue / orange" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-pnl", "cvd");
    expect(await pnlUp()).not.toBe(standard);
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-pnl", "cvd");
  });
});

test.describe("dialog focus", () => {
  test("focus is trapped, the page is hidden, and focus returns on Escape", async ({ page }) => {
    await page.goto("/trading/risk");
    const arm = page.getByTestId("kill-switch-arm");
    await arm.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog).toBeVisible();

    // Focus moved in, and Tab never leaves the dialog. (Base UI's focus
    // guards sit at the dialog's edges and hand focus back inside a moment
    // after they receive it, so each step is polled.)
    for (let i = 0; i < 12; i += 1) {
      await expect
        .poll(() => dialog.evaluate((el) => el.contains(document.activeElement)), {
          message: `focus left the dialog after ${i} Tab presses`,
          timeout: 1_000,
        })
        .toBe(true);
      await page.keyboard.press("Tab");
    }

    // Everything outside is hidden from assistive technology.
    await expect(page.locator(".shell")).toHaveAttribute("aria-hidden", "true");
    await expect(page.getByTestId("mode-banner")).toHaveAttribute("aria-hidden", "true");
    await expect(page.getByTestId("status-bar")).toHaveAttribute("aria-hidden", "true");

    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(arm).toBeFocused();
    await expect(page.locator(".shell")).not.toHaveAttribute("aria-hidden", "true");
  });

  test("choices are a radio group operable with arrow keys", async ({ page }) => {
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    const group = page.getByTestId("confirm-choices");
    await expect(group).toHaveAttribute("role", "radiogroup");
    const radios = group.getByRole("radio");
    await expect(radios).toHaveCount(2);
    await radios.first().focus();
    await page.keyboard.press("Space");
    await expect(page.getByTestId("confirm-choice-pause")).toHaveAttribute("data-selected", "true");
    await page.keyboard.press("ArrowRight");
    await expect(page.getByTestId("confirm-choice-flatten")).toHaveAttribute(
      "data-selected",
      "true",
    );
    await expect(page.getByTestId("confirm-choice-pause")).toHaveAttribute(
      "data-selected",
      "false",
    );
  });

  test("Escape and Cancel do nothing while a request is in flight", async ({ page }) => {
    let release: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let accepted = false;
    // The GET reflects the arm once the POST has been accepted, as the
    // platform would; the dialog closes when that is read back.
    await page.route(`${API}/api/system/kill-switch`, (route: Route) =>
      route.fulfill({
        json: accepted ? killSwitchView({ active: true, mode: "pause" }) : killSwitchView(),
      }),
    );
    await page.route(`${API}/api/system/kill-switch/arm`, async (route: Route) => {
      await gate;
      accepted = true;
      await route.fulfill({ json: killSwitchView({ active: true, mode: "pause" }) });
    });
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await page.getByTestId("confirm-choice-pause").click();
    await page.getByTestId("confirm-reason").fill("pausing while the feed recovers");
    await page.getByTestId("confirm-submit").click();
    await expect(page.getByTestId("confirm-submit")).toHaveText("Working…");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("confirm-dialog")).toBeVisible();
    await expect(page.getByTestId("confirm-cancel")).toBeDisabled();
    release?.();
    await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);
  });

  test("a backdrop click does not discard a typed reason", async ({ page }) => {
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await page.getByTestId("confirm-reason").fill("a reason worth keeping");
    await page.mouse.click(5, 400);
    await expect(page.getByTestId("confirm-dialog")).toBeVisible();
    await expect(page.getByTestId("confirm-reason")).toHaveValue("a reason worth keeping");
  });

  test("the inspector returns focus to the status bar", async ({ page }) => {
    await page.goto("/");
    const api = page.getByTestId("status-api");
    await api.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("inspector")).toBeVisible();
    await expect(page.getByTestId("inspector-health")).toContainText("database");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("inspector")).toHaveCount(0);
    await expect(api).toBeFocused();
  });

  test("a caveat popover opens from the keyboard and closes back to its badge", async ({
    page,
  }) => {
    await page.route(`${API}/api/trading/risk`, (route) =>
      route.fulfill({
        json: {
          data: {
            limits_version: "limits_v1_paper",
            kill_switch_active: false,
            kill_switch_mode: null,
            new_risk_permitted: true,
            new_risk_reason: "kill_switch_inactive",
            closing_permitted: true,
            daily_loss_pct: {
              value: -0.4,
              provenance: "paper",
              unit: "pct",
              as_of: null,
              sample_size: null,
              caveats: [],
              estimated: false,
            },
            max_daily_loss_pct: {
              value: 3,
              provenance: "paper",
              unit: "pct",
              as_of: null,
              sample_size: null,
              caveats: [],
              estimated: false,
            },
            drawdown_pct: {
              value: 2.1,
              provenance: "paper",
              unit: "pct",
              as_of: null,
              sample_size: null,
              caveats: [],
              estimated: true,
            },
            max_drawdown_limit_pct: {
              value: 20,
              provenance: "paper",
              unit: "pct",
              as_of: null,
              sample_size: null,
              caveats: [],
              estimated: false,
            },
            margin_utilisation_pct: {
              value: null,
              provenance: "paper",
              unit: "pct",
              as_of: null,
              sample_size: null,
              caveats: [
                {
                  code: "value_unavailable",
                  severity: "warning",
                  message: "No broker session exists in this mode.",
                  affects: "",
                  direction: "unknown",
                },
              ],
              estimated: false,
            },
            breaches: [],
          },
          source: { kind: "live", detail: "Limit set.", as_of: null },
          caveats: [],
        },
      }),
    );
    await page.goto("/trading/risk");
    const flag = page.getByTestId("figure-caveat-flag").first();
    await flag.focus();
    await page.keyboard.press("Enter");
    const popover = page.getByTestId("figure-caveat-popover");
    await expect(popover).toBeVisible();
    await expect(popover).toContainText("No broker session exists in this mode.");
    await expect(flag).toHaveAttribute("aria-expanded", "true");
    await page.keyboard.press("Escape");
    await expect(popover).toHaveCount(0);
    await expect(flag).toBeFocused();

    // `est` is a focusable popover too, not a hover title.
    const est = page.getByTestId("figure-estimated").first();
    await est.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Modelled, not observed at a venue.")).toBeVisible();
  });
});

test.describe("closing a popover leaves no dead zone (inventory F-6)", () => {
  // Base UI returns focus when a popup UNMOUNTS, at the end of its exit
  // transition. With an unconditional return, a control opened during that
  // transition had focus pulled back to the old trigger and closed: a click
  // in the first ~150 ms after Escape was swallowed. Transitions are slowed
  // tenfold here so the click lands inside the exit every time.
  test("a Select opened while a closed popover is still animating out stays open, with focus in it", async ({
    page,
  }) => {
    await page.goto("/system/legend?tab=controls");
    await expect(page.getByTestId("legend-controls")).toBeVisible();
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Animation.enable");
    await cdp.send("Animation.setPlaybackRate", { playbackRate: 0.1 });
    const trigger = page.getByRole("button", { name: "Popover", exact: true });
    const select = page.getByRole("combobox").first();
    await trigger.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    // The popover is still mounted, animating out.
    await expect(page.locator("[data-ending-style]")).toHaveCount(1);
    await select.click();
    // Let the popover finish closing (its focus return runs now).
    await expect(page.locator(".popover-body")).toHaveCount(0, { timeout: 10_000 });
    await expect(select).toHaveAttribute("aria-expanded", "true");
    const focusInList = await page.evaluate(
      () => document.activeElement?.closest('[role="listbox"]') !== null,
    );
    expect(focusInList, "focus stays in the Select the operator opened").toBe(true);
  });

  test("Escape on a popover still returns focus to its trigger when nothing else was touched", async ({ page }) => {
    await page.goto("/system/legend?tab=controls");
    await expect(page.getByTestId("legend-controls")).toBeVisible();
    const trigger = page.getByRole("button", { name: "Popover", exact: true });
    await trigger.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".popover-body")).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });
});

test.describe("tooltips", () => {
  test("open on keyboard focus", async ({ page }) => {
    await page.goto("/trading/risk");
    await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", "paper");
    await page.keyboard.press("Tab"); // skip link
    await page.keyboard.press("Tab"); // first rail icon
    await expect(page.getByTestId("rail-command")).toBeFocused();
    // A closing tooltip lingers for its exit transition; read the open one.
    const tooltip = page.locator('[data-testid="tooltip"][data-open]');
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toHaveText("Command");
    await page.keyboard.press("Tab");
    await expect(tooltip).toHaveText("Fleet & Positions");
    await page.keyboard.press("Escape");
    await expect(tooltip).toHaveCount(0);
  });
});

test.describe("tooltips on touch", () => {
  test.use({ hasTouch: true });
  test("open on tap", async ({ page }) => {
    await page.goto("/system/legend");
    await page.getByTestId("legend-tab-controls").tap();
    const button = page.getByRole("button", { name: "Settings example" });
    await button.tap();
    await expect(page.locator('[data-testid="tooltip"][data-open]')).toHaveText("Settings example");
  });
});

/** Every non-GET request the page makes, to prove a shortcut mutated nothing. */
function recordMutations(page: import("@playwright/test").Page): string[] {
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET") mutations.push(`${request.method()} ${request.url()}`);
  });
  return mutations;
}

test.describe("g chords", () => {
  for (const [key, path] of [
    ["l", "/trading/candidates"],
    ["x", "/trading/risk"],
    ["j", "/trading/execution"],
    ["s", "/system/services"],
    ["c", "/"],
  ] as const) {
    test(`g then ${key} goes to ${path}`, async ({ page }) => {
      await page.goto(path === "/" ? "/system/legend" : "/");
      await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", "paper");
      await page.keyboard.press("g");
      await page.keyboard.press(key);
      await expect.poll(() => new URL(page.url()).pathname).toBe(path);
    });
  }

  test("a chord typed into a field does nothing; a late second key does nothing", async ({
    page,
  }) => {
    await page.clock.install();
    await page.goto("/trading/execution");
    await expect(page.getByTestId("grid-filter")).toBeVisible();
    await page.getByTestId("grid-filter").focus();
    await page.keyboard.type("gl");
    await expect(page.getByTestId("grid-filter")).toHaveValue("gl");
    await expect(page).toHaveURL(/\/trading\/execution$/);
    await page.getByTestId("grid-filter").blur();
    await page.keyboard.press("g");
    await page.clock.fastForward(2_000);
    await page.keyboard.press("l");
    await page.waitForTimeout(300);
    await expect(page).toHaveURL(/\/trading\/execution$/);
  });
});

test.describe("command palette", () => {
  for (const modifier of ["Meta", "Control"]) {
    test(`${modifier}+K opens it; typing and Enter navigates`, async ({ page }) => {
      await page.goto("/");
      await page.keyboard.press(`${modifier}+KeyK`);
      const palette = page.getByTestId("command-palette");
      await expect(palette).toBeVisible();
      await expect(page.getByTestId("palette-input")).toBeFocused();
      await page.keyboard.type("Candidates");
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/\/trading\/candidates$/);
      await expect(palette).toHaveCount(0);
    });
  }

  test("open by id: a trade id selects that row on the trades screen", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("ControlOrMeta+KeyK");
    await page.getByTestId("palette-input").fill("trd_0003");
    await page.getByTestId("palette-open-trade").click();
    await expect(page).toHaveURL(/\/trading\/execution\?row=trd_0003$/);
    await expect(page.locator('[data-row-id="trd_0003"]')).toHaveAttribute("aria-selected", "true");
  });

  test("Escape closes it and returns focus", async ({ page }) => {
    await page.goto("/");
    const api = page.getByTestId("status-api");
    await api.focus();
    await page.keyboard.press("ControlOrMeta+KeyK");
    await expect(page.getByTestId("command-palette")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("command-palette")).toHaveCount(0);
    await expect(api).toBeFocused();
  });

  test("its kill-switch action only opens the dialog", async ({ page }) => {
    const mutations = recordMutations(page);
    await page.goto("/research");
    await page.keyboard.press("ControlOrMeta+KeyK");
    await page.getByTestId("palette-input").fill("halt");
    await page.getByTestId("palette-action-kill-switch").click();
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId("confirm-choice-pause")).toHaveAttribute("data-selected", "false");
    await expect(page.getByTestId("confirm-choice-flatten")).toHaveAttribute("data-selected", "false");
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    expect(mutations).toEqual([]);
  });
});

test.describe("? and ⇧K", () => {
  test("? lists every shortcut and every chord", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", "paper");
    await page.keyboard.press("Shift+Slash");
    const sheet = page.getByTestId("shortcut-sheet");
    await expect(sheet).toBeVisible();
    await expect(sheet).toContainText("Open the kill-switch dialog. It never arms by itself");
    for (const chord of ["c", "f", "l", "r", "m", "x", "d", "s", "j"]) {
      await expect(page.getByTestId(`shortcut-chord-${chord}`)).toBeVisible();
    }
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
  });

  test("⇧K opens the kill-switch dialog from any screen and arms nothing", async ({ page }) => {
    const mutations = recordMutations(page);
    await page.goto("/research/strategies");
    await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", "paper");
    await page.keyboard.press("Shift+KeyK");
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Halt trading");
    await expect(page.getByTestId("confirm-choice-pause")).toHaveAttribute("data-selected", "false");
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    // Typing K into the reason is text, not another shortcut.
    await page.getByTestId("confirm-reason").fill("Keep");
    await page.keyboard.press("Shift+KeyK");
    await expect(page.getByTestId("confirm-reason")).toHaveValue("KeepK");
    await page.getByTestId("confirm-cancel").click();
    await expect(dialog).toHaveCount(0);
    expect(mutations).toEqual([]);
  });

  test("⇧K with a role that cannot arm says why and opens nothing", async ({ page }) => {
    await page.route(`${API}/api/auth/me`, (route: Route) =>
      route.fulfill({ json: principal({ role: "viewer", can_arm_kill_switch: false }) }),
    );
    await page.goto("/");
    await expect(page.getByTestId("status-operator")).toHaveAttribute("data-role", "viewer");
    await page.keyboard.press("Shift+KeyK");
    await expect(page.getByTestId("toast")).toContainText("Kill switch:");
    await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);
  });
});
