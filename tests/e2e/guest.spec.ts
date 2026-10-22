// The guest journey against simulator and gateway: invite, dashboard, commands, admin preview, revocation.
import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";
import { INGRESS, PUBLIC_ORIGIN } from "./helpers/constants.mjs";
import { stackState } from "./helpers/gateway";

const ROOM_203 = ["Room 203 bedside", "Room 203 blind", "Room 203 ceiling", "Room 203 humidity", "Room 203 radiator"];
// shortcut: phase 1 of Task 8.5; MYHA_E2E_PHASE_2=1 runs these early, drop the skips once 6.3, 6.4 and 7.1 are on main.
const PHASE_2 = "needs the event stream, actions and admin preview (Tasks 6.3, 6.4, 7.1)";

const { joinUrls, guestState } = stackState();

/** Labels of the device cards on the dashboard. */
const cardLabels = (page: Page) =>
  page.locator("main [role=group][aria-label]").evaluateAll((cards) => cards.map((c) => c.getAttribute("aria-label")));

/** A guest API call from inside the page: only the browser holds the Secure session cookie for localhost. */
function guestAction(page: Page, body: { resource_id: string; action: string; parameters: Record<string, unknown> }) {
  return page.evaluate(async (body) => {
    const me = (await (await fetch("/api/v1/me")).json()) as { csrf_token: string };
    const answer = await fetch("/api/v1/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-MyHA-CSRF": me.csrf_token, "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify(body),
    });
    const json = (await answer.json()) as { error?: { code: string } };
    return { status: answer.status, code: json.error?.code ?? null };
  }, body);
}

/** Admin calls go node-side through the ingress stand-in, never from the guest page (another origin). */
async function admin(): Promise<{ api: APIRequestContext; headers: Record<string, string> }> {
  const api = await request.newContext({ baseURL: INGRESS.url });
  const session = await api.get("/api/v1/admin/session");
  expect(session.ok()).toBe(true);
  const { csrf_token } = (await session.json()) as { csrf_token: string };
  return { api, headers: { "X-MyHA-CSRF": csrf_token } };
}

test.describe.configure({ mode: "serial" }); // one guest, in order, ending with the revocation

test("(1) the invite link opens exactly the guest's room", async ({ page, context }) => {
  await page.goto(joinUrls[0]!);
  await expect(page).toHaveURL(`${PUBLIC_ORIGIN}/join`); // the secret left the address bar
  await page.getByRole("button", { name: "Open my room" }).click();
  await expect(page).toHaveURL(`${PUBLIC_ORIGIN}/`);
  await expect(page.getByRole("heading", { level: 1, name: "Room 203" })).toBeVisible();
  await expect.poll(async () => (await cardLabels(page)).sort()).toEqual(ROOM_203);
  await expect(page.getByText(/Hallway|Living room/)).toHaveCount(0);
  // __Host- cookie on plain http://localhost: Chromium counts localhost as a secure context.
  const cookie = (await context.cookies()).find((c) => c.name.startsWith("__Host-"));
  expect(cookie).toMatchObject({ secure: true, httpOnly: true, sameSite: "Lax", path: "/" });
  await context.storageState({ path: guestState });
});

test.describe("as the joined guest", () => {
  test.use({ storageState: guestState });

  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "Room 203" })).toBeVisible();
  });

  test("(2) turning the main light off is confirmed within 3 s", async ({ page }) => {
    test.skip(!process.env.MYHA_E2E_PHASE_2, PHASE_2);
    await expect(page.getByText("Connected")).toBeVisible(); // the event stream is up
    const light = page.getByRole("group", { name: "Room 203 ceiling" });
    // Every text the live region shows, also those that pass faster than an assertion polls.
    await light.locator("[aria-live]").evaluate((region) => {
      const seen: string[] = [];
      (window as unknown as { seen: string[] }).seen = seen;
      new MutationObserver(() => seen.push(region.textContent ?? "")).observe(region, { childList: true, subtree: true, characterData: true });
    });
    const started = Date.now();
    await light.getByRole("button", { name: "Turn off" }).click();
    await expect(light.getByText("Confirmed")).toBeVisible({ timeout: 3_000 });
    expect(Date.now() - started).toBeLessThanOrEqual(3_000);
    const seen = await page.evaluate(() => (window as unknown as { seen: string[] }).seen.join("\n"));
    expect(seen).toContain("Checking and sending…");
    await expect(light.getByRole("button", { name: "Turn on" })).toBeVisible();
  });

  test("(3) the radiator stops at its maximum; foreign and ungranted actions are refused", async ({ page }) => {
    test.skip(!process.env.MYHA_E2E_PHASE_2, PHASE_2);
    const radiator = page.getByRole("group", { name: "Room 203 radiator" });
    const raise = radiator.getByRole("button", { name: "Raise target" });
    for (let step = 0; step < 40 && (await raise.isEnabled()); step++) await raise.click();
    await expect(raise).toBeDisabled();
    expect(await guestAction(page, { resource_id: "res_203_bedside", action: "light.set_level", parameters: { level_pct: 50 } })).toEqual({
      status: 403,
      code: "ACTION_NOT_ALLOWED",
    });
    expect((await guestAction(page, { resource_id: "res_lr_ceiling", action: "light.on", parameters: {} })).code).toBe("NOT_FOUND");
  });

  test("(6) no horizontal scrolling at 320 and 1180 px", async ({ page }) => {
    for (const width of [320, 1180]) {
      await page.setViewportSize({ width, height: 800 });
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
        .toBe(0);
    }
  });

  test("(7) Tab reaches every button and Enter presses it", async ({ page }) => {
    // Buttons by card and label: the set may change while the page settles (a lock disables the controls).
    const focus = () =>
      page.evaluate(() => {
        const name = (b: Element) => `${b.closest("[role=group]")?.getAttribute("aria-label") ?? ""}|${b.getAttribute("aria-label") ?? b.textContent}`;
        const active = document.activeElement;
        return {
          active: active instanceof HTMLButtonElement ? name(active) : null,
          enabled: Array.from(document.querySelectorAll("button:not([disabled])"), name),
        };
      });
    const reached = new Set<string>();
    for (let press = 0; press < 80; press++) {
      await page.keyboard.press("Tab");
      const { active } = await focus();
      if (active) reached.add(active);
    }
    const { enabled } = await focus();
    expect(enabled.length).toBeGreaterThan(5);
    expect(enabled.filter((button) => !reached.has(button))).toEqual([]);
    await page.getByRole("button", { name: "Dark" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.getByRole("button", { name: "System" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", /./);
  });

  test("the ingress stand-in reaches the admin API as the owner, whatever the client claims", async () => {
    const api = await request.newContext({ baseURL: INGRESS.url, extraHTTPHeaders: { "X-Remote-User-Id": "intruder" } });
    const session = await api.get("/api/v1/admin/session");
    expect(session.status()).toBe(200);
    expect(((await session.json()) as { user: { id: string } }).user.id).toBe(INGRESS.userId);
    // The public listener knows no admin route at all.
    expect((await (await request.newContext({ baseURL: PUBLIC_ORIGIN })).get("/api/v1/admin/session")).status()).toBe(404);
  });

  test("(4) the admin previews the second invite and the simulation refuses 30 °C", async () => {
    test.skip(!process.env.MYHA_E2E_PHASE_2, PHASE_2);
    const { api, headers } = await admin();
    const { stays } = (await (await api.get("/api/v1/admin/stays")).json()) as {
      stays: { invites: { invite_id: string; redeemed: boolean }[] }[];
    };
    const second = stays.flatMap((s) => s.invites).find((i) => !i.redeemed);
    expect(second).toBeDefined();
    const created = await api.post("/api/v1/admin/preview", { headers, data: { target: { invite_id: second!.invite_id } } });
    expect(created.status()).toBe(201);
    const { id } = (await created.json()) as { id: string };
    const simulated = await api.post(`/api/v1/admin/preview/${id}/simulate`, {
      headers,
      data: { resource_id: "res_203_trv", action: "climate.set_temperature", parameters: { target_c: "30.0" } },
    });
    expect(((await simulated.json()) as { would_allow: boolean }).would_allow).toBe(false);
  });

  test("(5) revoking the session ends the guest page within 5 s", async ({ page }) => {
    test.skip(!process.env.MYHA_E2E_PHASE_2, PHASE_2);
    const { api, headers } = await admin();
    const { sessions } = (await (await api.get("/api/v1/admin/sessions")).json()) as {
      sessions: { id: string; revoked_at: string | null }[];
    };
    const live = sessions.filter((s) => s.revoked_at === null);
    expect(live).toHaveLength(1);
    expect((await api.post(`/api/v1/admin/sessions/${live[0]!.id}/revoke`, { headers })).status()).toBe(204);
    await expect(page.getByRole("heading", { name: "Access ended" })).toBeVisible({ timeout: 5_000 });
  });
});
