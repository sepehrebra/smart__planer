import { test, expect, type Page } from "@playwright/test";
const session = {
  user: { id: "user", email: "planner@example.com", timezone: "Asia/Tehran" },
  csrf_token: "a".repeat(64),
};
const prefs = {
  version: 1,
  chronotype: "neutral",
  focus_block_minutes: 45,
  break_minutes: 10,
  flexibility: "medium",
  workload: "medium",
  deep_work_period: "afternoon",
};
const tasks = [
  {
    id: "11111111-1111-4111-a111-111111111111",
    title: "مرور محاسبات علمی",
    description: "",
    duration_minutes: 60,
    priority: 8,
    status: "pending",
    version: 1,
    preferred_period: "any",
    splittable: false,
    earliest_start: null,
    deadline: null,
  },
  {
    id: "22222222-2222-4222-a222-222222222222",
    title: "تمرین زبان انگلیسی",
    description: "",
    duration_minutes: 45,
    priority: 6,
    status: "pending",
    version: 1,
    preferred_period: "afternoon",
    splittable: false,
    earliest_start: null,
    deadline: null,
  },
];
async function mock(page: Page) {
  await page.route("**/api/v1/**", async (r) => {
    const p = new URL(r.request().url()).pathname;
    const data = p.endsWith("/auth/session")
      ? session
      : p.endsWith("/me/preferences")
        ? prefs
        : p.endsWith("/tasks")
          ? tasks
          : [];
    await r.fulfill({ json: data });
  });
}
test("RTL dashboard, tasks and mobile layout remain usable", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "برنامهٔ من." }),
  ).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await page.getByRole("button", { name: "انتخاب همه", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "پیش‌نمایش برنامه", exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: "test-results/dashboard-desktop.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "کارهای من", exact: false })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "مرور محاسبات علمی", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "ویرایش مرور محاسبات علمی" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "بازکردن فهرست" }).click();
  await page.getByRole("button", { name: "برنامهٔ من", exact: true }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "test-results/dashboard-mobile.png",
    fullPage: true,
  });
});
test("ambiguous task create retries same ID and keeps form contents", async ({
  page,
}) => {
  await mock(page);
  let first = "",
    attempt = 0;
  await page.route("**/api/v1/tasks", async (r) => {
    if (r.request().method() !== "POST") return r.fallback();
    const data = r.request().postDataJSON();
    expect(r.request().headers()["x-csrf-token"]).toBe(session.csrf_token);
    if (!attempt++) {
      first = data.client_request_id;
      await r.abort("failed");
    } else {
      expect(data.client_request_id).toBe(first);
      await r.fulfill({
        status: 201,
        json: { ...tasks[0], title: data.title },
      });
    }
  });
  await page.goto("/");
  await page.getByRole("button", { name: "افزودن کار", exact: true }).click();
  await page.getByRole("button", { name: "کار جدید", exact: true }).click();
  await page
    .getByRole("textbox", { name: "عنوان کار", exact: true })
    .fill("کار آزمایشی");
  await page.getByRole("button", { name: "ذخیرهٔ کار", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("ارتباط قطع شد");
  await expect(
    page.getByRole("textbox", { name: "عنوان کار", exact: true }),
  ).toHaveValue("کار آزمایشی");
  await page.getByRole("button", { name: "ذخیرهٔ کار", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
test("preview is visibly unsaved and a rejected save preserves it", async ({
  page,
}) => {
  await mock(page);
  await page.route("**/api/v1/schedules/preview", async (r) => {
    const body = r.request().postDataJSON();
    await r.fulfill({
      json: {
        blocks: [
          {
            task_id: tasks[0].id,
            start: body.availability[0].start,
            end: new Date(
              Date.parse(body.availability[0].start) + 3600000,
            ).toISOString(),
            locked: false,
          },
        ],
        task_versions: { [tasks[0].id]: 1 },
        preference_version: 1,
        unscheduled: [],
        warnings: [],
        fixed_events: [],
      },
    });
  });
  await page.route("**/api/v1/schedules", (r) =>
    r.fulfill({
      status: 409,
      json: {
        code: "stored_fixed_event_conflict",
        message: "زمان کار با تعهد ثابت فعلی تداخل دارد.",
      },
    }),
  );
  await page.goto("/");
  await page
    .getByRole("checkbox", { name: "مرور محاسبات علمی", exact: false })
    .check();
  await page
    .getByRole("button", { name: "پیش‌نمایش برنامه", exact: true })
    .click();
  await expect(page.getByText("ذخیره‌نشده", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "test-results/plan-preview.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "ذخیرهٔ برنامه", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("تداخل");
  await expect(page.getByText("ذخیره‌نشده", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "مرور محاسبات علمی", exact: true }),
  ).toBeVisible();
  page.on("dialog", (d) => d.dismiss());
  await page
    .getByRole("button", { name: "کلاس‌ها و جلسه‌ها", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "برنامهٔ من." }),
  ).toBeVisible();
});
test("login failure and server outage are visible without pretending success", async ({
  page,
}) => {
  await page.route("**/api/v1/auth/session", (r) =>
    r.fulfill({ status: 401, json: { message: "وارد حساب شوید." } }),
  );
  await page.route("**/api/v1/auth/login", (r) =>
    r.fulfill({ status: 401, json: { message: "ایمیل یا رمز درست نیست." } }),
  );
  await page.goto("/");
  await page.getByLabel("ایمیل", { exact: true }).fill("test@example.com");
  await page
    .getByLabel("رمز عبور", { exact: false })
    .fill("wrong password value");
  await page
    .getByRole("button", { name: "ورود به برنامه", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("ایمیل یا رمز درست نیست");
  await expect(page.getByRole("heading", { name: "خوش برگشتی" })).toBeVisible();
  await page.screenshot({ path: "test-results/login.png", fullPage: true });
});
test("uncertain fixed-event creation is not blindly repeated", async ({
  page,
}) => {
  await mock(page);
  await page.route("**/api/v1/fixed-events", (r) => r.abort("failed"));
  await page.goto("/");
  await page
    .getByRole("button", { name: "کلاس‌ها و جلسه‌ها", exact: true })
    .click();
  await page.getByRole("button", { name: "تعهد جدید", exact: true }).click();
  await page.getByRole("textbox", { name: "عنوان تعهد" }).fill("جلسه");
  await page.getByRole("button", { name: "ذخیرهٔ تعهد", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "ذخیرهٔ تعهد", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "بررسی فهرست تعهدها" }),
  ).toBeVisible();
});
