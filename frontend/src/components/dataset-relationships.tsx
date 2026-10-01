"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { datasets, type Dataset, type Relationship } from "@/lib/api";

type SchemaTable = { name: string; columns: { name: string }[] };

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";

const sameKey = (a: Relationship, b: Relationship) =>
  [a.from_table, a.from_column, a.to_table, a.to_column].join("\u0000").toLowerCase() ===
  [b.from_table, b.from_column, b.to_table, b.to_column].join("\u0000").toLowerCase();

export function describeRelationship(item: Relationship) {
  const many = item.kind === "one_to_one" ? "each" : "many";
  return `${item.from_table}.${item.from_column} → ${item.to_table}.${item.to_column} (${many} ${item.from_table} rows to one ${item.to_table} row)`;
}

export function DatasetRelationships({ dataset }: { dataset: Dataset }) {
  const client = useQueryClient();
  const tables = ((dataset.schema as { tables?: SchemaTable[] })?.tables ?? []).filter(
    (table) => table.columns.length > 0,
  );
  const [items, setItems] = useState<Relationship[]>(dataset.relationships?.relationships ?? []);
  const [dirty, setDirty] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [form, setForm] = useState({
    from_table: tables[0]?.name ?? "",
    from_column: "",
    to_table: tables[1]?.name ?? "",
    to_column: "",
    kind: "many_to_one" as Relationship["kind"],
  });
  const suggestions = useQuery({
    queryKey: ["relationship-suggestions", dataset.id, dataset.current_version_id],
    queryFn: () => datasets.relationshipSuggestions(dataset.id),
    enabled: showSuggestions,
  });
  if (tables.length < 2) return null;

  const columnsOf = (name: string) => tables.find((table) => table.name === name)?.columns ?? [];
  const open = (suggestions.data?.suggestions ?? []).filter(
    (item) => !items.some((existing) => sameKey(existing, item.relationship)),
  );
  const change = (next: Relationship[]) => {
    setItems(next);
    setDirty(true);
  };
  const add = (item: Relationship) => {
    if (items.some((existing) => sameKey(existing, item))) {
      toast.error("That relationship is already in the list");
      return;
    }
    change([...items, item]);
  };

  async function save() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const saved = await datasets.saveRelationships(dataset.id, items);
      setItems(saved.relationships?.relationships ?? []);
      setDirty(false);
      await client.invalidateQueries({ queryKey: ["dataset", dataset.id] });
      await client.invalidateQueries({ queryKey: ["relationship-suggestions", dataset.id] });
      toast.success("Relationships saved");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save relationships");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const fromColumn = form.from_column || columnsOf(form.from_table)[0]?.name || "";
  const toColumn = form.to_column || columnsOf(form.to_table)[0]?.name || "";
  const canAddManual =
    Boolean(form.from_table && form.to_table && fromColumn && toColumn) &&
    form.from_table !== form.to_table;

  return (
    <Card data-testid="table-relationships">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>Table relationships</CardTitle>
        {dataset.kind !== "connection" && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setShowSuggestions((value) => !value)}
          >
            {showSuggestions ? "Hide suggestions" : "Suggest joins"}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p className="text-muted-foreground">
          Approved relationships tell the AI which columns link your tables, so questions that need
          more than one table join them on the right keys. Suggestions come from fixed checks on
          column names, repeated values and matching values, not from an AI model. Only table and
          column names are shared with the AI.
        </p>
        {showSuggestions && (
          <div className="space-y-2 rounded-md border p-3" data-testid="join-suggestions">
            <h3 className="font-medium">Suggested joins</h3>
            {suggestions.isLoading && <p className="text-muted-foreground">Checking the data...</p>}
            {suggestions.isError && (
              <p role="alert" className="text-destructive">
                {suggestions.error instanceof Error
                  ? suggestions.error.message
                  : "Could not check the data"}
              </p>
            )}
            {suggestions.data && open.length === 0 && (
              <p className="text-muted-foreground">No further suggestions.</p>
            )}
            {open.map((item) => (
              <div key={item.relationship.id} className="flex flex-wrap items-center gap-2">
                <span>{describeRelationship(item.relationship)}</span>
                <Badge variant="outline">{Math.round(item.match_share * 100)}% match</Badge>
                <span className="text-muted-foreground">({item.reason})</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => add(item.relationship)}
                >
                  Add relationship
                </Button>
              </div>
            ))}
          </div>
        )}
        {items.length === 0 ? (
          <p>No relationships yet.</p>
        ) : (
          <ul className="space-y-1" data-testid="relationship-list">
            {items.map((item, index) => (
              <li
                key={`${item.id}-${index}`}
                className="flex flex-wrap items-center gap-2 rounded-md border p-2"
              >
                <span className="flex-1">{describeRelationship(item)}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove relationship ${index + 1}`}
                  onClick={() => change(items.filter((_, position) => position !== index))}
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}
        <details className="rounded-md border p-3">
          <summary className="cursor-pointer font-medium">Add a relationship by hand</summary>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {(
              [
                ["from_table", "from_column", "Table with many rows", "Its key column"],
                ["to_table", "to_column", "Table with one row per key", "Its key column"],
              ] as const
            ).map(([tableKey, columnKey, tableLabel, columnLabel]) => (
              <div key={tableKey} className="space-y-2">
                <label className="block space-y-1">
                  <span className="text-xs text-muted-foreground">{tableLabel}</span>
                  <select
                    id={`rel-${tableKey}`}
                    className={selectClass}
                    value={form[tableKey]}
                    onChange={(event) =>
                      setForm({ ...form, [tableKey]: event.target.value, [columnKey]: "" })
                    }
                  >
                    {tables.map((table) => (
                      <option key={table.name} value={table.name}>
                        {table.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block space-y-1">
                  <span className="text-xs text-muted-foreground">{columnLabel}</span>
                  <select
                    id={`rel-${columnKey}`}
                    className={selectClass}
                    value={columnKey === "from_column" ? fromColumn : toColumn}
                    onChange={(event) => setForm({ ...form, [columnKey]: event.target.value })}
                  >
                    {columnsOf(form[tableKey]).map((column) => (
                      <option key={column.name} value={column.name}>
                        {column.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            ))}
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">How many rows per key</span>
              <select
                id="rel-kind"
                className={selectClass}
                value={form.kind}
                onChange={(event) =>
                  setForm({ ...form, kind: event.target.value as Relationship["kind"] })
                }
              >
                <option value="many_to_one">Many to one</option>
                <option value="one_to_one">One to one</option>
              </select>
            </label>
          </div>
          {form.from_table === form.to_table && (
            <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
              Choose two different tables.
            </p>
          )}
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="mt-3"
            disabled={!canAddManual}
            onClick={() =>
              add({
                id: Math.random().toString(16).slice(2, 14),
                from_table: form.from_table,
                from_column: fromColumn,
                to_table: form.to_table,
                to_column: toColumn,
                kind: form.kind,
              })
            }
          >
            Add to list
          </Button>
        </details>
        <Button type="button" disabled={busy || !dirty} onClick={save}>
          {busy ? "Saving..." : "Save relationships"}
        </Button>
      </CardContent>
    </Card>
  );
}
