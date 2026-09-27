import { test, expect } from "@playwright/test";
test.skip(
  !process.env.E2E_NATIVE,
  "Requires a disposable native PostgreSQL backend.",
);
test("real account → tasks/class → preview/save → move → reload → undo/redo → replan", async ({
  page,
}) => {
  test.setTimeout(90000);
  await page.goto("/");
  await page.getByRole("button", { name: "ساخت حساب", exact: true }).click();
  await page
    .getByLabel("ایمیل", { exact: true })
    .fill(`web-${crypto.randomUUID()}@example.com`);
  await page
    .getByLabel("رمز عبور", { exact: false })
    .fill("Frontend acceptance 2026!");
  await page
    .getByRole("button", { name: "ساخت حساب و شروع", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "برنامهٔ من." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "افزودن کار", exact: true }).click();
  await page.getByRole("button", { name: "کار جدید", exact: true }).click();
  await page
    .getByRole("textbox", { name: "عنوان کار", exact: true })
    .fill("مرور جبر خطی");
  await page.getByRole("button", { name: "ذخیرهٔ کار", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const date = new Date(Date.now() + 86400000 * 5).toISOString().slice(0, 10);
  await page
    .getByRole("button", { name: "کلاس‌ها و جلسه‌ها", exact: true })
    .click();
  await page.getByRole("button", { name: "تعهد جدید", exact: true }).click();
  await page.getByRole("textbox", { name: "عنوان تعهد" }).fill("کلاس دانشگاه");
  await page.getByLabel("شروع", { exact: false }).fill(date + "T10:00");
  await page.getByLabel("پایان", { exact: true }).fill(date + "T12:00");
  await page.getByRole("button", { name: "ذخیرهٔ تعهد", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "برنامهٔ من", exact: true }).click();
  await page.getByLabel("تاریخ شروع", { exact: false }).fill(date);
  await page.getByRole("button", { name: "انتخاب همه", exact: true }).click();
  await page
    .getByRole("button", { name: "پیش‌نمایش برنامه", exact: true })
    .click();
  await expect(page.getByText("ذخیره‌نشده", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "ذخیرهٔ برنامه", exact: true })
    .click();
  await expect(page.getByText("ذخیره‌شده", { exact: true })).toBeVisible();
  await expect(
    page.getByLabel("برنامهٔ ذخیره‌شده", { exact: true }),
  ).not.toHaveValue("");
  const id = await page
    .getByLabel("برنامهٔ ذخیره‌شده", { exact: true })
    .inputValue();
  expect(id).toBeTruthy();
  await page
    .getByRole("button", { name: "جابه‌جایی مرور جبر خطی", exact: true })
    .click();
  await page.getByLabel("شروع جدید", { exact: false }).fill(date + "T14:00");
  await page.getByRole("button", { name: "اعمال در پیش‌نمایش" }).click();
  await page
    .getByRole("button", { name: "ذخیرهٔ برنامه", exact: true })
    .click();
  await expect(page.getByText("نسخهٔ ۲", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByLabel("برنامهٔ ذخیره‌شده", { exact: true }).selectOption(id);
  await expect(page.getByText("نسخهٔ ۲", { exact: true })).toBeVisible();
  await expect(page.getByText("۱۴:۰۰", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "برگشت", exact: true }).click();
  await expect(page.getByText("نسخهٔ ۳", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "اعمال مجدد", exact: true }).click();
  await expect(page.getByText("نسخهٔ ۴", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "بازچینی پیشنهادی", exact: true })
    .click();
  await expect(page.getByText("ذخیره‌نشده", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "ذخیرهٔ برنامه", exact: true })
    .click();
  await expect(page.getByText("نسخهٔ ۵", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "test-results/native-saved-plan.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "ریتم شخصی", exact: true }).click();
  await page
    .getByLabel("حجم کار روزانه", { exact: true })
    .selectOption("light");
  await page
    .getByRole("button", { name: "ذخیرهٔ ترجیح‌ها", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("ذخیره شد");
  await page
    .getByRole("button", { name: "کارهای من", exact: false })
    .first()
    .click();
  page.once("dialog", (d) => d.accept());
  await page
    .getByRole("button", { name: "حذف مرور جبر خطی", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "مرور جبر خطی", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "برنامهٔ من", exact: true }).click();
  await page.getByLabel("برنامهٔ ذخیره‌شده", { exact: true }).selectOption(id);
  await expect(
    page.getByText("این برنامه به بررسی نیاز دارد.", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "بازچینی پیشنهادی", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("کارهای حذف‌شده");
  await page
    .getByRole("button", { name: "ذخیرهٔ برنامه", exact: true })
    .click();
  await expect(page.getByText("نسخهٔ ۶", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "مرور جبر خطی", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "خروج از حساب", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "خوش برگشتی", exact: true }),
  ).toBeVisible();
});
