let csrf = "";
const pending = new Map<string, string>();
export function setCsrf(value: string) {
  csrf = value;
  if (!value) pending.clear();
}
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, {
      method,
      credentials: "same-origin",
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" && csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(
      0,
      "network",
      "ارتباط قطع شد؛ نتیجهٔ درخواست مشخص نیست. اطلاعات را تازه کنید یا همان درخواست را دوباره بفرستید.",
    );
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 && !path.startsWith("/auth/"))
      window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(
      response.status,
      data.code || "request_failed",
      data.message || "درخواست انجام نشد؛ دوباره تلاش کنید.",
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
// Reuse a request ID after an ambiguous network failure; successful commands get a new ID next time.
export async function command<T>(
  path: string,
  method: string,
  body: object,
): Promise<T> {
  const key = JSON.stringify([path, method, body]);
  const id = pending.get(key) || crypto.randomUUID();
  pending.set(key, id);
  const result = await api<T>(path, method, { ...body, client_request_id: id });
  pending.delete(key);
  return result;
}
export async function allPages<T>(path: string): Promise<T[]> {
  const items: T[] = [];
  for (let offset = 0; offset <= 100000; offset += 100) {
    const page = await api<T[]>(
      path + (path.includes("?") ? "&" : "?") + `limit=100&offset=${offset}`,
    );
    items.push(...page);
    if (page.length < 100) return items;
  }
  throw new Error("تعداد اطلاعات بیش از محدودهٔ نمایش است.");
}
export function message(error: unknown) {
  return error instanceof Error
    ? error.message
    : "خطای ناشناخته؛ دوباره تلاش کنید.";
}
