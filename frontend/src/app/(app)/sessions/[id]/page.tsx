"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { RunCard } from "@/components/run-card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { datasets, health, runs, sessions, type Run } from "@/lib/api";
import { streamRunEvents, type RunEvent } from "@/lib/sse";

async function waitForTerminalRun(runId: string): Promise<Run> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const run = await runs.get(runId);
    if (["completed", "failed"].includes(run.status)) return run;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Run completion was not persisted in time");
}

export default function SessionPage() {
  const id = String(useParams().id);
  const client = useQueryClient();
  const [goal, setGoal] = useState("");
  const [sending, setSending] = useState(false);
  const [mode, setMode] = useState<"quick" | "deep">("quick");
  const [live, setLive] = useState<Record<string, RunEvent[]>>({});
  const [pending, setPending] = useState<Run[]>([]);
  const query = useQuery({
    queryKey: ["session", id],
    queryFn: () => sessions.get(id),
    refetchInterval: (result) =>
      pending.length > 0 ||
      result.state.data?.runs?.some((run) => ["pending", "running"].includes(run.status))
        ? 1000
        : false,
  });
  const dataset = useQuery({
    queryKey: ["session-dataset", query.data?.dataset_id],
    queryFn: () => datasets.get(query.data!.dataset_id),
    enabled: Boolean(query.data),
  });
  const localModel = useQuery({ queryKey: ["health"], queryFn: health }).data?.local_model;
  async function send(clarified?: { goal: string }) {
    const text = clarified?.goal ?? goal;
    if (!text.trim() || sending) return;
    setSending(true);
    let createdRunId: string | undefined;
    if (!clarified) setGoal("");
    try {
      const run = await sessions.createRun(id, text, mode, Boolean(clarified));
      createdRunId = run.id;
      setPending((items) => [...items, run]);
      await client.invalidateQueries({ queryKey: ["session", id] });
      await streamRunEvents(run.id, (event) =>
        setLive((value) => ({ ...value, [run.id]: [...(value[run.id] ?? []), event] })),
      );
      const complete = await waitForTerminalRun(run.id);
      setPending((items) => items.filter((item) => item.id !== run.id));
      client.setQueryData(["session", id], (value: typeof query.data) =>
        value
          ? {
              ...value,
              runs: [
                ...(value.runs ?? []).filter((existing) => existing.id !== complete.id),
                complete,
              ],
            }
          : value,
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Run failed");
    } finally {
      if (createdRunId) {
        setPending((items) => items.filter((item) => item.id !== createdRunId));
        await client.invalidateQueries({ queryKey: ["session", id] });
      }
      setSending(false);
    }
  }
  const allRuns = Array.from(
    new Map([...pending, ...(query.data?.runs ?? [])].map((run) => [run.id, run])).values(),
  );
  return (
    <div className="mx-auto max-w-5xl space-y-6 pb-28">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold">{query.data?.title ?? "Analysis session"}</h1>
        <p className="text-sm font-medium">
          Optional exploratory follow-up chat — not an approved metric report
        </p>
        {dataset.data && (
          <>
            <Link
              className="text-sm text-muted-foreground hover:underline"
              href={`/datasets/${dataset.data.id}`}
            >
              {dataset.data.name}
            </Link>
            <div className="rounded-md border p-3 text-sm text-muted-foreground">
              {dataset.data.llm_policy === "local"
                ? localModel
                  ? `Local only: chat uses ${localModel} on this computer; nothing is sent to a cloud AI.`
                  : "Local only: chat uses a generic profiling plan. Saved reports use the deterministic report engine."
                : dataset.data.llm_policy === "schema_only"
                  ? "Schema-only cloud planning: values and previous answer text stay local."
                  : "Full cloud analysis: questions, result rows, statistics and summaries may be shared."}{" "}
              <Link className="underline" href={`/datasets/${dataset.data.id}`}>
                Change data sharing
              </Link>
            </div>
          </>
        )}
      </header>
      <div className="space-y-8">
        {allRuns.length ? (
          allRuns.map((run) => (
            <RunCard
              key={run.id}
              run={run}
              events={live[run.id]}
              answering={sending}
              onAnswer={(question, answer) =>
                send({ goal: `${run.goal}\n\nClarification: ${question} ${answer}` })
              }
            />
          ))
        ) : (
          <div className="rounded-xl border border-dashed p-12 text-center text-muted-foreground">
            Ask a question about your data to begin.
          </div>
        )}
      </div>
      <div className="fixed inset-x-0 bottom-0 border-t bg-background/95 p-4 backdrop-blur md:left-[240px]">
        <div className="mx-auto flex max-w-5xl gap-3">
          <Textarea
            placeholder="Ask a question about this dataset..."
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
          />
          <div className="flex flex-col gap-2">
            <select
              aria-label="Analysis mode"
              title="Deep mode checks each result and lets the AI revise its queries before summarizing"
              className="h-8 rounded-md border bg-background px-2 text-xs"
              value={mode}
              onChange={(event) => setMode(event.target.value as "quick" | "deep")}
            >
              <option value="quick">Quick</option>
              <option value="deep">Deep: check and revise</option>
            </select>
            <Button className="flex-1" disabled={sending || !goal.trim()} onClick={() => send()}>
              {sending ? "Analyzing..." : "Send"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
