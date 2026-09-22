import { API_BASE, runs, type Run } from "./api";

export type RunEvent = {
  type: string;
  name?: string;
  action?: string;
  artifact?: Record<string, unknown>;
  plan?: Record<string, unknown>;
  message?: string;
};

export async function streamRunEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
  signal?: AbortSignal,
) {
  const token = localStorage.getItem("if_token");
  try {
    const response = await fetch(`${API_BASE}/runs/${runId}/events`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal,
    });
    if (!response.ok || !response.body) throw new Error("Event stream unavailable");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const line = frame.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        const event = JSON.parse(line.slice(6)) as RunEvent;
        onEvent(event);
        if (["done", "error"].includes(event.type)) return;
      }
    }
  } catch (error) {
    if (signal?.aborted) throw error;
    while (!signal?.aborted) {
      const run: Run = await runs.get(runId);
      if (["completed", "failed"].includes(run.status)) {
        onEvent({
          type: run.status === "completed" ? "done" : "error",
          message: run.error ?? undefined,
        });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }
}
