"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { datasets, type ApprovedQuery, type Dataset } from "@/lib/api";

export function DatasetQueries({ dataset }: { dataset: Dataset }) {
  const client = useQueryClient();
  const [items, setItems] = useState<ApprovedQuery[]>(dataset.queries?.queries ?? []);
  const [approvedOnly, setApprovedOnly] = useState(Boolean(dataset.queries?.approved_only));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [form, setForm] = useState({ question: "", sql: "", description: "" });

  const change = (next: ApprovedQuery[]) => {
    setItems(next);
    setDirty(true);
  };

  function add() {
    const question = form.question.trim();
    const sql = form.sql.trim();
    if (question.length < 3 || !sql) {
      toast.error("Enter a question and the SQL that answers it");
      return;
    }
    if (items.some((item) => item.question.toLowerCase() === question.toLowerCase())) {
      toast.error("That question is already saved");
      return;
    }
    change([
      ...items,
      {
        id: Math.random().toString(16).slice(2, 14),
        question,
        sql,
        description: form.description.trim(),
        approved: true,
      },
    ]);
    setForm({ question: "", sql: "", description: "" });
  }

  async function save() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const saved = await datasets.saveQueries(dataset.id, items, approvedOnly);
      setItems(saved.queries?.queries ?? []);
      setApprovedOnly(Boolean(saved.queries?.approved_only));
      setDirty(false);
      await client.invalidateQueries({ queryKey: ["dataset", dataset.id] });
      toast.success("Approved questions saved");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save the questions");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <Card data-testid="dataset-queries">
      <CardHeader>
        <CardTitle>Approved questions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p className="text-muted-foreground">
          Save a question with the SQL that answers it. When someone asks the same thing (in other
          words too), the saved SQL runs exactly as approved and the result is labelled as an
          approved query. Use &quot;Save as approved question&quot; under a result to add one from
          an answer you checked.
        </p>
        <label className="flex items-start gap-2 rounded-md border p-3">
          <input
            type="checkbox"
            checked={approvedOnly}
            onChange={(event) => {
              setApprovedOnly(event.target.checked);
              setDirty(true);
            }}
          />
          <span>
            <span className="font-medium">Approved data only</span>
            <span className="block text-xs text-muted-foreground">
              Answer only from approved metrics and approved questions. Anything else is refused
              with a list of what can be asked; the AI never writes SQL of its own.
            </span>
          </span>
        </label>
        {items.length === 0 ? (
          <p>No approved questions yet.</p>
        ) : (
          <ul className="space-y-2" data-testid="query-list">
            {items.map((item, index) => (
              <li key={item.id ?? index} className="space-y-1 rounded-md border p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{item.question}</span>
                  <Badge variant={item.approved ? "default" : "outline"}>
                    {item.approved ? "Approved" : "Draft"}
                  </Badge>
                  <span className="flex-1" />
                  <label className="flex items-center gap-1 text-xs">
                    <input
                      type="checkbox"
                      checked={Boolean(item.approved)}
                      onChange={(event) =>
                        change(
                          items.map((query, position) =>
                            position === index
                              ? { ...query, approved: event.target.checked }
                              : query,
                          ),
                        )
                      }
                    />
                    Approved
                  </label>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove question ${item.question}`}
                    onClick={() => change(items.filter((_, position) => position !== index))}
                  >
                    Remove
                  </Button>
                </div>
                {item.description ? (
                  <p className="text-xs text-muted-foreground">{item.description}</p>
                ) : null}
                <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">{item.sql}</pre>
              </li>
            ))}
          </ul>
        )}
        <details className="rounded-md border p-3">
          <summary className="cursor-pointer font-medium">Add a question</summary>
          <div className="mt-3 space-y-2">
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">Question</span>
              <Input
                id="query-question"
                value={form.question}
                onChange={(event) => setForm({ ...form, question: event.target.value })}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">SQL (one read-only query)</span>
              <textarea
                id="query-sql"
                className="min-h-24 w-full rounded-md border bg-background p-2 font-mono text-xs"
                value={form.sql}
                onChange={(event) => setForm({ ...form, sql: event.target.value })}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">Description (optional)</span>
              <Input
                id="query-description"
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
              />
            </label>
            <Button type="button" size="sm" variant="outline" onClick={add}>
              Add question
            </Button>
          </div>
        </details>
        <div className="flex items-center gap-2">
          <Button type="button" onClick={save} disabled={!dirty || busy}>
            {busy ? "Saving..." : "Save approved questions"}
          </Button>
          {dirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
        </div>
      </CardContent>
    </Card>
  );
}
