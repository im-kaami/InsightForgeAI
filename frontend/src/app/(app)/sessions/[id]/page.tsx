"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { RunCard } from "@/components/run-card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { datasets, runs, sessions, type Run } from "@/lib/api";
import { streamRunEvents, type RunEvent } from "@/lib/sse";

export default function SessionPage() {
  const id = String(useParams().id);
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["session", id], queryFn: () => sessions.get(id) });
  const dataset = useQuery({
    queryKey: ["session-dataset", query.data?.dataset_id],
    queryFn: () => datasets.get(query.data!.dataset_id),
    enabled: Boolean(query.data),
  });
  const [goal, setGoal] = useState("");
  const [sending, setSending] = useState(false);
  const [live, setLive] = useState<Record<string, RunEvent[]>>({});
  const [pending, setPending] = useState<Run[]>([]);
  async function send() {
    if (!goal.trim()) return;
    setSending(true);
    const text = goal;
    setGoal("");
    try {
      const run = await sessions.createRun(id, text);
      setPending((items) => [...items, run]);
      await streamRunEvents(run.id, (event) =>
        setLive((value) => ({ ...value, [run.id]: [...(value[run.id] ?? []), event] })),
      );
      const complete = await runs.get(run.id);
      setPending((items) => items.filter((item) => item.id !== run.id));
      client.setQueryData(["session", id], (value: typeof query.data) =>
        value ? { ...value, runs: [...(value.runs ?? []), complete] } : value,
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Run failed");
    } finally {
      setSending(false);
    }
  }
  const allRuns = [...(query.data?.runs ?? []), ...pending];
  return (
    <div className="mx-auto max-w-5xl space-y-6 pb-28">
      <header>
        <h1 className="text-3xl font-semibold">{query.data?.title ?? "Analysis session"}</h1>
        {dataset.data && (
          <Link
            className="text-sm text-muted-foreground hover:underline"
            href={`/datasets/${dataset.data.id}`}
          >
            {dataset.data.name}
          </Link>
        )}
      </header>
      <div className="space-y-8">
        {allRuns.length ? (
          allRuns.map((run) => <RunCard key={run.id} run={run} events={live[run.id]} />)
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
          <Button className="h-auto" disabled={sending || !goal.trim()} onClick={send}>
            {sending ? "Analyzing..." : "Send"}
          </Button>
        </div>
      </div>
    </div>
  );
}
