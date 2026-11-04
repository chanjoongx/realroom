import type { AppConfig, Demo, Run } from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export const runFile = (id: string, name: string) => `${BASE}/runs/${encodeURIComponent(id)}/${name}`;

/** The widths of the smaller WebP copies the server makes of a run's photos (VARIANT_WIDTHS in server.py). */
const VARIANT_WIDTHS = [640, 960, 1280];
/** The widest photo a run holds: uploads are sent at most this wide, and the demo rooms are this wide. */
const FULL_W = 1792;

/** srcset for a run photo (/runs/<id>/<name>.jpg or .png): the server's WebP copies up to maxW, and the
 *  file itself when maxW allows it, so a phone downloads about a sixth of the bytes. Other URLs get none.
 *  The lossless files behind the pixel check are always fetched whole, by their plain URL. */
export function photoSrcSet(url: string | undefined, maxW = FULL_W): string | undefined {
  if (!url || !/\/runs\/[^/?#]+\/[^/?#]+\.(jpg|png)$/.test(url)) return undefined;
  const set = VARIANT_WIDTHS.filter((w) => w <= maxW).map((w) => `${url}?w=${w} ${w}w`);
  if (maxW >= FULL_W) set.push(`${url} ${FULL_W}w`);
  return set.join(", ");
}

/** The plain src to pair with photoSrcSet(url, maxW): the widest copy it lists. */
export function photoSrc(url: string, maxW = FULL_W): string {
  const w = VARIANT_WIDTHS.filter((v) => v <= maxW).pop();
  return maxW >= FULL_W || w === undefined || !photoSrcSet(url) ? url : `${url}?w=${w}`;
}
export const thumbUrl = (pid: string) => `${BASE}/catalog/thumb/${pid}.webp`;
export const imageUrl = (pid: string) => `${BASE}/catalog/img/${pid}.jpg`;

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export async function getRun(id: string, signal?: AbortSignal): Promise<Run> {
  return json<Run>(await fetch(`${BASE}/api/runs/${encodeURIComponent(id)}`, { signal, cache: "no-store" }));
}

export async function getDemos(signal?: AbortSignal): Promise<Demo[]> {
  return json<Demo[]>(await fetch(`${BASE}/api/demos`, { signal }));
}

export async function getConfig(signal?: AbortSignal): Promise<AppConfig> {
  return json<AppConfig>(await fetch(`${BASE}/api/config`, { signal, cache: "no-store" }));
}

/** A fetch failure in words a visitor can act on. */
export function netMessage(e: unknown): string {
  if (e instanceof DOMException && (e.name === "AbortError" || e.name === "TimeoutError")) return "The server took too long to answer.";
  if (e instanceof TypeError) return "Could not reach the server. Check your connection.";
  return e instanceof Error ? e.message : String(e);
}

export async function startRun(photo: Blob, brief: string, mode: string): Promise<{ id: string }> {
  const fd = new FormData();
  fd.append("photo", photo, "room.jpg");
  fd.append("brief", brief);
  fd.append("mode", mode);
  return json<{ id: string }>(await fetch(`${BASE}/api/runs`, { method: "POST", body: fd }));
}
