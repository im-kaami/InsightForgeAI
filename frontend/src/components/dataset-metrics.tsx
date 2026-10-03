"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { DataTable } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  datasets,
  type Dataset,
  type Metric,
  type MetricFilter,
  type MetricPreview,
} from "@/lib/api";

type SchemaTable = { name: string; columns: { name: string; dtype: string }[] };

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";
const AGGREGATIONS: { value: Metric["aggregation"]; label: string }[] = [
  { value: "sum", label: "Sum of a column" },
  { value: "count", label: "Number of rows" },
  { value: "count_distinct", label: "Number of distinct values" },
  { value: "avg", label: "Average of a column" },
  { value: "min", label: "Smallest value" },
  { value: "max", label: "Largest value" },
];
const isNumber = (dtype: string) =>
  /INT|FLOAT|DOUBLE|DECIMAL|NUMERIC|REAL/i.test(dtype) && !/INTERVAL/i.test(dtype);
const isDate = (dtype: string) => /^(DATE|TIMESTAMP)/i.test(dtype) || /VARCHAR/i.test(dtype);

function describeFilter(item: MetricFilter) {
  const value = Array.isArray(item.value) ? item.value.join(", ") : String(item.value);
  if (item.op === "in") return `${item.column} is one of ${value}`;
  return `${item.column} ${item.op === "not_equals" ? "is not" : "equals"} ${value}`;
}

export function describeMetric(metric: Metric) {
  const what = AGGREGATIONS.find((item) => item.value === metric.aggregation)?.label ?? "";
  let text = `${what}${metric.column ? ` (${metric.column})` : ""} in ${metric.table}`;
  if (metric.filters?.length)
    text += `, only where ${metric.filters.map(describeFilter).join(" and ")}`;
  return text;
}

const emptyForm = (table: string) => ({
  name: "",
  label: "",
  description: "",
  unit: "",
  synonyms: "",
  table,
  aggregation: "sum" as Metric["aggregation"],
  column: "",
  date_column: "",
  dimensions: [] as string[],
  filters: [] as MetricFilter[],
  approved: false,
  id: "",
});

type Form = ReturnType<typeof emptyForm>;

const toForm = (metric: Metric): Form => ({
  name: metric.name,
  label: metric.label ?? "",
  description: metric.description ?? "",
  unit: metric.unit ?? "",
  synonyms: (metric.synonyms ?? []).join(", "),
  table: metric.table,
  aggregation: metric.aggregation,
  column: metric.column ?? "",
  date_column: metric.date_column ?? "",
  dimensions: metric.dimensions ?? [],
  filters: metric.filters ?? [],
  approved: Boolean(metric.approved),
  id: metric.id ?? "",
});

const fromForm = (form: Form): Metric => ({
  id: form.id || Math.random().toString(16).slice(2, 14),
  name: form.name.trim(),
  label: form.label.trim(),
  description: form.description.trim(),
  unit: form.unit.trim(),
  synonyms: form.synonyms
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean),
  table: form.table,
  aggregation: form.aggregation,
  column: form.aggregation === "count" ? null : form.column || null,
  date_column: form.date_column || null,
  dimensions: form.dimensions,
  filters: form.filters,
  approved: form.approved,
});

export function DatasetMetrics({ dataset }: { dataset: Dataset }) {
  const client = useQueryClient();
  const tables = ((dataset.schema as { tables?: SchemaTable[] })?.tables ?? []).filter(
    (table) => table.columns.length > 0,
  );
  const relationships = dataset.relationships?.relationships ?? [];
  const [items, setItems] = useState<Metric[]>(dataset.metrics?.metrics ?? []);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [form, setForm] = useState<Form>(emptyForm(tables[0]?.name ?? ""));
  const [filter, setFilter] = useState({
    column: "",
    op: "equals" as MetricFilter["op"],
    value: "",
  });
  const [preview, setPreview] = useState<{ name: string; result: MetricPreview } | null>(null);
  const suggestions = useQuery({
    queryKey: ["metric-suggestions", dataset.id, dataset.current_version_id],
    queryFn: () => datasets.metricSuggestions(dataset.id),
    enabled: showSuggestions,
  });
  if (tables.length === 0) return null;

  const columnsOf = (name: string) => tables.find((table) => table.name === name)?.columns ?? [];
  const baseColumns = columnsOf(form.table);
  const joined = relationships
    .filter((item) => item.from_table === form.table)
    .flatMap((item) => columnsOf(item.to_table).map((column) => `${item.to_table}.${column.name}`));
  const dimensionChoices = [...baseColumns.map((column) => column.name), ...joined];
  const valueColumns =
    form.aggregation === "sum" || form.aggregation === "avg"
      ? baseColumns.filter((column) => isNumber(column.dtype))
      : baseColumns;
  const filterColumn = filter.column || baseColumns[0]?.name || "";

  const change = (next: Metric[]) => {
    setItems(next);
    setDirty(true);
  };

  function addFilter() {
    if (!filter.value.trim()) return;
    const value =
      filter.op === "in"
        ? filter.value
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean)
        : filter.value.trim();
    setForm({
      ...form,
      filters: [...form.filters, { column: filterColumn, op: filter.op, value }],
    });
    setFilter({ ...filter, value: "" });
  }

  function submitForm() {
    const metric = fromForm(form);
    if (!/^[A-Za-z][A-Za-z0-9_]{0,59}$/.test(metric.name)) {
      toast.error("Name: start with a letter; use letters, digits and underscores only");
      return;
    }
    if (form.aggregation !== "count" && !metric.column) {
      toast.error("Choose the column to calculate");
      return;
    }
    const clash = items.some(
      (item, index) => index !== editing && item.name.toLowerCase() === metric.name.toLowerCase(),
    );
    if (clash) {
      toast.error("Another metric already has that name");
      return;
    }
    change(
      editing === null
        ? [...items, metric]
        : items.map((item, index) => (index === editing ? metric : item)),
    );
    setEditing(null);
    setForm(emptyForm(tables[0]?.name ?? ""));
  }

  async function save() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const saved = await datasets.saveMetrics(dataset.id, items);
      setItems(saved.metrics?.metrics ?? []);
      setDirty(false);
      await client.invalidateQueries({ queryKey: ["dataset", dataset.id] });
      await client.invalidateQueries({ queryKey: ["metric-suggestions", dataset.id] });
      toast.success("Metrics saved");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save metrics");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function runPreview(metric: Metric) {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      setPreview({ name: metric.name, result: await datasets.previewMetric(dataset.id, metric) });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not calculate the metric");
    } finally {
      inFlight.current = false;
    }
  }

  const open = (suggestions.data?.suggestions ?? []).filter(
    (item) => !items.some((existing) => existing.name.toLowerCase() === item.name.toLowerCase()),
  );

  return (
    <Card data-testid="dataset-metrics">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>Metrics</CardTitle>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setShowSuggestions((value) => !value)}
        >
          {showSuggestions ? "Hide suggestions" : "Suggest metrics"}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p className="text-muted-foreground">
          Define the numbers your business uses, such as revenue, once. When a question names an
          approved metric (or one of its other names), the AI only chooses the groupings, filters
          and period; tested code writes the SQL, and the result is labelled as an approved metric.
          Drafts are never used. Groupings from another table need an approved table relationship.
        </p>
        {showSuggestions && (
          <div className="space-y-2 rounded-md border p-3" data-testid="metric-suggestions">
            <h3 className="font-medium">Suggested drafts</h3>
            <p className="text-xs text-muted-foreground">
              Drafted from column names and types only; review them before approving.
            </p>
            {suggestions.isLoading && <p className="text-muted-foreground">Loading...</p>}
            {suggestions.data && open.length === 0 && (
              <p className="text-muted-foreground">No further suggestions.</p>
            )}
            {open.map((item) => (
              <div key={item.name} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{item.label || item.name}</span>
                <span className="text-muted-foreground">{describeMetric(item)}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => change([...items, { ...item, approved: false }])}
                >
                  Add as draft
                </Button>
              </div>
            ))}
          </div>
        )}
        {items.length === 0 ? (
          <p>No metrics yet.</p>
        ) : (
          <ul className="space-y-2" data-testid="metric-list">
            {items.map((item, index) => (
              <li key={`${item.name}-${index}`} className="space-y-2 rounded-md border p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{item.label || item.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">{item.name}</span>
                  <Badge variant={item.approved ? "default" : "outline"}>
                    {item.approved ? "Approved" : "Draft"}
                  </Badge>
                  {item.unit ? <Badge variant="outline">{item.unit}</Badge> : null}
                  <span className="flex-1" />
                  <label className="flex items-center gap-1 text-xs">
                    <input
                      type="checkbox"
                      checked={Boolean(item.approved)}
                      onChange={(event) =>
                        change(
                          items.map((metric, position) =>
                            position === index
                              ? { ...metric, approved: event.target.checked }
                              : metric,
                          ),
                        )
                      }
                    />
                    Approved for the AI
                  </label>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => runPreview(item)}
                  >
                    Preview
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEditing(index);
                      setForm(toForm(item));
                    }}
                  >
                    Edit
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove metric ${item.name}`}
                    onClick={() => change(items.filter((_, position) => position !== index))}
                  >
                    Remove
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {describeMetric(item)}
                  {item.dimensions?.length ? `; group by ${item.dimensions.join(", ")}` : ""}
                  {item.date_column ? `; periods by ${item.date_column}` : ""}
                  {item.synonyms?.length ? `; also called ${item.synonyms.join(", ")}` : ""}
                </p>
                {preview?.name === item.name && (
                  <div data-testid="metric-preview" className="space-y-1">
                    <p className="text-xs">{preview.result.description}</p>
                    <DataTable
                      columns={preview.result.columns}
                      rows={preview.result.rows as Record<string, unknown>[]}
                      totalRows={preview.result.total_rows}
                      sql={preview.result.sql}
                    />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        <details className="rounded-md border p-3" open={editing !== null || undefined}>
          <summary className="cursor-pointer font-medium">
            {editing === null ? "Add a metric" : `Edit ${items[editing]?.name ?? "metric"}`}
          </summary>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">Name (letters, digits, _)</span>
              <Input
                id="metric-name"
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">Label</span>
              <Input
                id="metric-label"
                value={form.label}
                onChange={(event) => setForm({ ...form, label: event.target.value })}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">Table</span>
              <select
                id="metric-table"
                className={selectClass}
                value={form.table}
                onChange={(event) =>
                  setForm({
                    ...form,
                    table: event.target.value,
                    column: "",
                    date_column: "",
                    dimensions: [],
                    filters: [],
                  })
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
              <span className="text-xs text-muted-foreground">Calculation</span>
              <select
                id="metric-aggregation"
                className={selectClass}
                value={form.aggregation}
                onChange={(event) =>
                  setForm({
                    ...form,
                    aggregation: event.target.value as Metric["aggregation"],
                    column: "",
                  })
                }
              >
                {AGGREGATIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            {form.aggregation !== "count" && (
              <label className="block space-y-1">
                <span className="text-xs text-muted-foreground">Column to calculate</span>
                <select
                  id="metric-column"
                  className={selectClass}
                  value={form.column}
                  onChange={(event) => setForm({ ...form, column: event.target.value })}
                >
                  <option value="">Choose a column</option>
                  {valueColumns.map((column) => (
                    <option key={column.name} value={column.name}>
                      {column.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">Date column (optional)</span>
              <select
                id="metric-date"
                className={selectClass}
                value={form.date_column}
                onChange={(event) => setForm({ ...form, date_column: event.target.value })}
              >
                <option value="">None</option>
                {baseColumns
                  .filter((column) => isDate(column.dtype))
                  .map((column) => (
                    <option key={column.name} value={column.name}>
                      {column.name}
                    </option>
                  ))}
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">Unit (optional)</span>
              <Input
                id="metric-unit"
                value={form.unit}
                onChange={(event) => setForm({ ...form, unit: event.target.value })}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-xs text-muted-foreground">Other names (comma-separated)</span>
              <Input
                id="metric-synonyms"
                value={form.synonyms}
                onChange={(event) => setForm({ ...form, synonyms: event.target.value })}
              />
            </label>
            <label className="block space-y-1 md:col-span-2">
              <span className="text-xs text-muted-foreground">Description</span>
              <Input
                id="metric-description"
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
              />
            </label>
          </div>
          <fieldset className="mt-3 space-y-1">
            <legend className="text-xs text-muted-foreground">Allowed groupings and filters</legend>
            <div className="flex flex-wrap gap-3">
              {dimensionChoices.map((name) => (
                <label key={name} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={form.dimensions.includes(name)}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        dimensions: event.target.checked
                          ? [...form.dimensions, name]
                          : form.dimensions.filter((item) => item !== name),
                      })
                    }
                  />
                  {name}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="mt-3 space-y-2">
            <p className="text-xs text-muted-foreground">Always count only rows where</p>
            {form.filters.map((item, index) => (
              <div key={`${item.column}-${index}`} className="flex items-center gap-2">
                <span>{describeFilter(item)}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setForm({
                      ...form,
                      filters: form.filters.filter((_, position) => position !== index),
                    })
                  }
                >
                  Remove
                </Button>
              </div>
            ))}
            <div className="grid gap-2 md:grid-cols-4">
              <select
                aria-label="Filter column"
                className={selectClass}
                value={filterColumn}
                onChange={(event) => setFilter({ ...filter, column: event.target.value })}
              >
                {baseColumns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}
                  </option>
                ))}
              </select>
              <select
                aria-label="Filter condition"
                className={selectClass}
                value={filter.op}
                onChange={(event) =>
                  setFilter({ ...filter, op: event.target.value as MetricFilter["op"] })
                }
              >
                <option value="equals">equals</option>
                <option value="not_equals">is not</option>
                <option value="in">is one of (comma-separated)</option>
              </select>
              <Input
                aria-label="Filter value"
                value={filter.value}
                onChange={(event) => setFilter({ ...filter, value: event.target.value })}
              />
              <Button type="button" size="sm" variant="outline" onClick={addFilter}>
                Add condition
              </Button>
            </div>
          </div>
          <label className="mt-3 flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.approved}
              onChange={(event) => setForm({ ...form, approved: event.target.checked })}
            />
            I checked this definition; the AI may use it
          </label>
          <div className="mt-3 flex gap-2">
            <Button type="button" size="sm" variant="outline" onClick={submitForm}>
              {editing === null ? "Add to list" : "Update metric"}
            </Button>
            {editing !== null && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setEditing(null);
                  setForm(emptyForm(tables[0]?.name ?? ""));
                }}
              >
                Cancel
              </Button>
            )}
          </div>
        </details>
        <Button type="button" disabled={busy || !dirty} onClick={save}>
          {busy ? "Saving..." : "Save metrics"}
        </Button>
      </CardContent>
    </Card>
  );
}
