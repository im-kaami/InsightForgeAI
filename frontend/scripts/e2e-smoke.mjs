import { readFile } from "node:fs/promises";
import { basename, resolve } from "node:path";

const api = "http://localhost:8000/api";
async function request(path, options = {}, token) {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`${api}${path}`, { ...options, headers });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.status === 204 ? null : response.json();
}

const credentials = { email: "demo@example.com", password: "demo12345" };
const register = await fetch(`${api}/auth/register`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(credentials),
});
if (!register.ok && register.status !== 409) throw new Error(await register.text());
const login = await request("/auth/login", { method: "POST", body: JSON.stringify(credentials) });
const token = login.access_token;
const form = new FormData();
for (const relative of [
  "../backend/tests/fixtures/hr.csv",
  "../backend/tests/fixtures/workbook.xlsx",
]) {
  const path = resolve(relative);
  form.append("files", new File([await readFile(path)], basename(path)));
}
form.append("name", "Demo workforce data");
const dataset = await request("/datasets/upload", { method: "POST", body: form }, token);
const session = await request(
  "/sessions",
  { method: "POST", body: JSON.stringify({ dataset_id: dataset.id, title: "Demo analysis" }) },
  token,
);
const run = await request(
  `/sessions/${session.id}/runs`,
  {
    method: "POST",
    body: JSON.stringify({ goal: "Compare departments and summarize the workforce" }),
  },
  token,
);
const response = await fetch(`${api}/runs/${run.id}/events`, {
  headers: { Authorization: `Bearer ${token}` },
});
const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = "";
let terminal = false;
while (!terminal) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const frames = buffer.split("\n\n");
  buffer = frames.pop() ?? "";
  for (const frame of frames) {
    const line = frame.split("\n").find((item) => item.startsWith("data: "));
    if (!line) continue;
    const event = JSON.parse(line.slice(6));
    console.log("event", event.type, event.name ?? "");
    terminal = ["done", "error"].includes(event.type);
  }
}
let finalRun;
for (let attempt = 0; attempt < 20; attempt += 1) {
  finalRun = await request(`/runs/${run.id}`, {}, token);
  if (["completed", "failed"].includes(finalRun.status)) break;
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
}
console.log(
  JSON.stringify(
    {
      dataset: dataset.name,
      tables: dataset.tables,
      session: session.id,
      run: finalRun.id,
      status: finalRun.status,
      artifactTypes: [...new Set(finalRun.artifacts.map((item) => item.type))],
    },
    null,
    2,
  ),
);
