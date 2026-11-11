/** Same-origin API client. Session is an HttpOnly cookie; CSRF uses the double-submit cookie. */

export class ApiError extends Error {
  status: number;
  code?: string;
  items?: string[];
  constructor(status: number, message: string, code?: string, items?: string[]) {
    super(message);
    this.status = status;
    this.code = code;
    this.items = items;
  }
}

function readCookie(name: string): string | null {
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

let csrfPromise: Promise<void> | null = null;
async function ensureCsrf() {
  if (readCookie("nb_csrf")) return;
  csrfPromise ??= fetch("/v1/auth/csrf", { credentials: "same-origin" }).then(() => undefined);
  await csrfPromise;
  csrfPromise = null;
}

async function parseError(res: Response): Promise<ApiError> {
  let body: unknown = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  const d = (body as { detail?: unknown })?.detail;
  if (typeof d === "string") return new ApiError(res.status, d);
  if (d && typeof d === "object") {
    const o = d as { message?: string; code?: string; items?: string[] };
    return new ApiError(res.status, o.message ?? "Request failed", o.code, o.items);
  }
  if (res.status === 413) return new ApiError(413, "The file is too large.");
  if (res.status >= 500) return new ApiError(res.status, "The service is unavailable. Please try again shortly.");
  return new ApiError(res.status, "Request failed");
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const method = (init.method ?? (init.json !== undefined ? "POST" : "GET")).toUpperCase();
  const headers = new Headers(init.headers);
  if (method !== "GET") {
    await ensureCsrf();
    headers.set("X-CSRF-Token", readCookie("nb_csrf") ?? "");
  }
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    res = await fetch(path, { ...init, method, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "Network error — check your connection and try again.");
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Upload with real byte progress (fetch cannot report upload progress). */
export async function uploadWithProgress<T>(path: string, form: FormData, onProgress: (pct: number) => void): Promise<T> {
  await ensureCsrf();
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-CSRF-Token", readCookie("nb_csrf") ?? "");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload."));
    xhr.onload = async () => {
      const res = new Response(xhr.responseText, { status: xhr.status, headers: { "Content-Type": "application/json" } });
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText) as T);
      else reject(await parseError(res));
    };
    xhr.send(form);
  });
}
