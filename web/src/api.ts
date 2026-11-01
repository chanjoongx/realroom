import type { AppConfig, Demo, Run } from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export const runFile = (id: string, name: string) => `${BASE}/runs/${encodeURIComponent(id)}/${name}`;
export const catalogUrl = (path: string) => `${BASE}/${path}`;
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

export async function getDemos(): Promise<Demo[]> {
  return json<Demo[]>(await fetch(`${BASE}/api/demos`));
}

export async function getConfig(): Promise<AppConfig> {
  return json<AppConfig>(await fetch(`${BASE}/api/config`, { cache: "no-store" }));
}

export async function startRun(photo: Blob, brief: string, mode: string): Promise<{ id: string }> {
  const fd = new FormData();
  fd.append("photo", photo, "room.jpg");
  fd.append("brief", brief);
  fd.append("mode", mode);
  return json<{ id: string }>(await fetch(`${BASE}/api/runs`, { method: "POST", body: fd }));
}
