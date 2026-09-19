# SmartPlanner — پایهٔ برنامه‌ریز شخصی

[![PostgreSQL tests](https://github.com/sepehrebra/smart__planer/actions/workflows/tests.yml/badge.svg)](https://github.com/sepehrebra/smart__planer/actions/workflows/tests.yml)

نسخهٔ ۰٫۲: حساب کاربری، ورود و خروج، شش ترجیح برنامه‌ریزی و ثبت/خواندن/ویرایش/حذف کارها با FastAPI و PostgreSQL.

مثال: کار «انگلیسی، ۶۰ دقیقه» ذخیره می‌شود و پس از شروع دوباره باقی می‌ماند. هر کاربر فقط کارهای خودش را می‌بیند. تلاش دوبارهٔ همان درخواست کار تکراری نمی‌سازد و ویرایش نسخهٔ قدیمی خطای تعارض می‌دهد.

این مرحله رابط تقویم، موتور برنامه‌ریز یا اتصال AI ندارد. وضعیت واقعی آزمون‌ها و محدودیت انتشار در [BUILD_STATUS](docs/BUILD_STATUS.md) ثبت شده است.

## اجرا روی رایانه با Docker

پیش‌نیاز: Python 3.12+ برای ساخت تنظیمات و Docker Desktop/Engine همراه Compose.

```bash
python scripts/init_env.py
docker compose up --build
```

API روی `http://localhost:8000` و راهنمای تعاملی مسیرها روی `http://localhost:8000/docs` است. هنوز صفحهٔ محصول ساخته نشده است. برای درخواست‌های تغییر پس از ورود، مقدار `csrf_token` پاسخ ورود را در هدر `X-CSRF-Token` بفرستید؛ کوکی نشست هم باید همراه درخواست باشد. مستندات Swagger به‌تنهایی این هدر را خودکار اضافه نمی‌کند.

`docker compose down` دادهٔ ذخیره‌شده را نگه می‌دارد. برای حفظ اطلاعات از گزینهٔ `-v` استفاده نکنید. پیکربندی Compose مخصوص توسعه روی localhost است؛ استقرار عمومی مرحلهٔ جداگانه دارد.

## اجرا با PostgreSQL موجود

یک پایگاه توسعهٔ خالی بسازید. سپس در پوشهٔ پروژه:

```bash
python -m venv .venv
```

پس از فعال‌کردن محیط مجازی:

```bash
python -m pip install -e '.[test]'
```

متغیر `DATABASE_URL` را به اتصال پایگاه خود تنظیم کنید. نمونهٔ PowerShell با مقادیر جایگزین:

```powershell
$env:DATABASE_URL = 'postgresql://USER:PASSWORD@localhost:5432/smartplanner'
$env:APP_ORIGIN = 'http://localhost:8000'
python -m smartplanner.migrate
uvicorn smartplanner.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

## آزمون‌ها

فقط آزمون مدل‌ها، بدون پایگاه داده:

```bash
python -m unittest discover -s tests -p test_contracts.py -v
```

برای آزمون کامل، یک پایگاه **آزمایشی مستقل** انتخاب کنید. آزمون‌ها حساب و دادهٔ ساختگی اضافه می‌کنند و migration را اعمال می‌کنند.

```powershell
$env:SMARTPLANNER_TEST_DATABASE_URL = 'postgresql://USER:PASSWORD@localhost:5432/smartplanner_test'
$env:SMARTPLANNER_TEST_DB_FLAVOR = 'native'
python scripts/run_tests.py
```

GitHub Actions همین آزمون‌ها را با PostgreSQL 17 اجرا می‌کند؛ سپس پایگاه را دوباره راه می‌اندازد و ماندگاری حساب و کار را بررسی می‌کند.

## رفتارهای مهم

- ایجاد کار: `client_request_id` یک UUID است؛ هنگام تکرار همان درخواست تغییر نکند.
- ایجاد جدید 201 می‌دهد؛ تکرار معتبر 200 و همان شناسهٔ کار را برمی‌گرداند.
- `PATCH` به `expected_version` نیاز دارد؛ نسخهٔ قدیمی 409 می‌گیرد.
- حذف به `expected_version` در query نیاز دارد و فعلاً نرم است؛ تلاش قدیمی برای ایجاد دوباره، کار حذف‌شده را زنده نمی‌کند.
- خواندن کارِ کاربر دیگر مثل کار ناموجود 404 می‌دهد.
- زمان‌ها باید اختلاف ساعت مشخص داشته باشند؛ مثلاً `2026-09-20T18:00:00+03:30`.
- پس از ورود، `GET /api/v1/auth/session` توکن CSRF همین نشست را برای تازه‌سازی صفحه برمی‌گرداند.
- رمز خام و توکن خام نشست در پایگاه داده ذخیره نمی‌شوند. خطاهای ورودی، مقدار رمز را بازتاب نمی‌دهند.
- سقف تلاش ورود در این نسخه برای هر پردازش است؛ پیکربندی انتشار، بازیابی رمز، تأیید ایمیل و سیاست حذف قطعی داده هنوز تکمیل نشده‌اند.

## راهنما

- [طرح قدم دوم](docs/SmartPlanner_Step_02.md): معماری، مدل داده، راهبرد زمان‌بندی و مسیر ساخت.
- [قدم سوم](docs/SmartPlanner_Step_03.md): تصمیم‌های ذخیره‌سازی، حساب و کنترل ویرایش‌ها.
- [وضعیت ساخت و آزمون](docs/BUILD_STATUS.md): تفاوت قابلیت ساخته‌شده، آزموده‌شده و آمادهٔ انتشار.
