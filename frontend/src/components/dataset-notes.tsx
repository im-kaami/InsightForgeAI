"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { datasets, type Dataset } from "@/lib/api";

type ColumnNote = { description: string; unit: string; synonyms: string };

export function DatasetNotes({ dataset }: { dataset: Dataset }) {
  const queryClient = useQueryClient();
  const saved = dataset.notes ?? { general: "", columns: {} };
  const keys = (dataset.schema?.tables ?? []).flatMap((table) =>
    table.columns.map((column) => `${table.name}.${column.name}`),
  );
  const [general, setGeneral] = useState(saved.general ?? "");
  const [columns, setColumns] = useState<Record<string, ColumnNote>>(() =>
    Object.fromEntries(
      keys.map((key) => {
        const note = saved.columns?.[key];
        return [
          key,
          {
            description: note?.description ?? "",
            unit: note?.unit ?? "",
            synonyms: (note?.synonyms ?? []).join(", "),
          },
        ];
      }),
    ),
  );
  const [busy, setBusy] = useState(false);

  function update(key: string, change: Partial<ColumnNote>) {
    setColumns((current) => ({ ...current, [key]: { ...current[key], ...change } }));
  }

  async function save() {
    setBusy(true);
    try {
      const payload = {
        general,
        columns: Object.fromEntries(
          Object.entries(columns)
            .map(([key, note]) => [
              key,
              {
                description: note.description.trim(),
                unit: note.unit.trim(),
                synonyms: note.synonyms
                  .split(",")
                  .map((item) => item.trim())
                  .filter(Boolean),
              },
            ])
            .filter(
              ([, note]) =>
                typeof note !== "string" && (note.description || note.unit || note.synonyms.length),
            ),
        ),
      };
      await datasets.saveNotes(dataset.id, payload);
      await queryClient.invalidateQueries({ queryKey: ["dataset", dataset.id] });
      toast.success("Notes saved");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save notes");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card data-testid="dataset-notes">
      <CardHeader>
        <CardTitle>Notes for the AI</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Explain what columns mean, their units, other names people use, and business rules. The AI
          reads these notes when planning and summarizing, in every mode that uses an AI model,
          including schema-only. Do not put confidential values in them.
        </p>
        <div className="space-y-2">
          <Label htmlFor="general-notes">General notes and business rules</Label>
          <Textarea
            id="general-notes"
            maxLength={4000}
            placeholder="For example: revenue means the amount of completed orders only."
            value={general}
            onChange={(event) => setGeneral(event.target.value)}
          />
        </div>
        {keys.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-xs text-muted-foreground">
                  <th className="py-1 pr-2 font-normal">Column</th>
                  <th className="py-1 pr-2 font-normal">Meaning</th>
                  <th className="py-1 pr-2 font-normal">Unit</th>
                  <th className="py-1 font-normal">Other names (comma-separated)</th>
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => (
                  <tr key={key} className="border-t">
                    <td className="py-1 pr-2 font-mono text-xs">{key}</td>
                    <td className="py-1 pr-2">
                      <input
                        aria-label={`Meaning of ${key}`}
                        maxLength={300}
                        className="h-8 w-full rounded-md border bg-background px-2"
                        value={columns[key]?.description ?? ""}
                        onChange={(event) => update(key, { description: event.target.value })}
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <input
                        aria-label={`Unit of ${key}`}
                        maxLength={40}
                        className="h-8 w-24 rounded-md border bg-background px-2"
                        value={columns[key]?.unit ?? ""}
                        onChange={(event) => update(key, { unit: event.target.value })}
                      />
                    </td>
                    <td className="py-1">
                      <input
                        aria-label={`Other names for ${key}`}
                        className="h-8 w-full rounded-md border bg-background px-2"
                        value={columns[key]?.synonyms ?? ""}
                        onChange={(event) => update(key, { synonyms: event.target.value })}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Button type="button" disabled={busy} onClick={save}>
          {busy ? "Saving..." : "Save notes"}
        </Button>
      </CardContent>
    </Card>
  );
}
