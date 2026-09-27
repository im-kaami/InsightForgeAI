"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  type Dataset,
  type DatasetVersion,
  type ReportDefinition,
  type SalesDefinition,
  verifiedReports,
} from "@/lib/api";

type Join = { table: string; fact_key: string; lookup_key: string; cardinality: "one_to_one" };
type Filter = { column: string; operator: "equals" | "not_equals"; value: string };

function NativeSelect({
  id,
  value,
  onChange,
  children,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <select
      id={id}
      className="h-9 w-full rounded-md border bg-background px-3 text-sm"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {children}
    </select>
  );
}

export function VerifiedReportBuilder({
  dataset,
  versions,
}: {
  dataset: Dataset;
  versions: DatasetVersion[];
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const definitions = useQuery({
    queryKey: ["report-definitions", dataset.id],
    queryFn: () => verifiedReports.list(dataset.id),
    enabled: dataset.kind !== "connection" && Boolean(dataset.current_version_id),
  });
  const tables = dataset.schema.tables;
  const [name, setName] = useState("");
  const [factTable, setFactTable] = useState(tables.length === 1 ? tables[0].name : "");
  const [rowKey, setRowKey] = useState("");
  const [orderId, setOrderId] = useState("");
  const [dateColumn, setDateColumn] = useState("");
  const [dateFormat, setDateFormat] = useState("%Y-%m-%d");
  const [revenueColumn, setRevenueColumn] = useState("");
  const [refunds, setRefunds] = useState("");
  const [cost, setCost] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [currencyColumn, setCurrencyColumn] = useState("");
  const [decimals, setDecimals] = useState(2);
  const [notes, setNotes] = useState("");
  const [noRefunds, setNoRefunds] = useState(false);
  const [singleCurrency, setSingleCurrency] = useState(false);
  const [joins, setJoins] = useState<Join[]>([]);
  const [filters, setFilters] = useState<Filter[]>([]);
  const [approvedSignature, setApprovedSignature] = useState<string | null>(null);
  const [previousId, setPreviousId] = useState<string | null>(null);
  const [selectedDefinition, setSelectedDefinition] = useState("");
  const [selectedVersion, setSelectedVersion] = useState(dataset.current_version_id ?? "");
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [busy, setBusy] = useState(false);

  if (dataset.kind === "connection") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Verified spreadsheet report</CardTitle>
        </CardHeader>
        <CardContent>
          Saved verified reports currently require an uploaded or URL snapshot.
        </CardContent>
      </Card>
    );
  }
  if (!dataset.current_version_id) return null;

  const factColumns = tables.find((table) => table.name === factTable)?.columns ?? [];
  const joinedTables = joins.map((join) => join.table).filter(Boolean);
  const amountTables = tables.filter(
    (table) => table.name === factTable || joinedTables.includes(table.name),
  );
  const readyVersions = versions.filter((version) => version.state === "ready");
  const required = Boolean(name && factTable && rowKey && dateColumn && revenueColumn && currency);
  const assumptions = Boolean((refunds || noRefunds) && (currencyColumn || singleCurrency));
  const joinsComplete = joins.every((join) => join.table && join.fact_key && join.lookup_key);
  const filtersComplete = filters.every((filter) => filter.column);

  function columnOptions(columns = factColumns) {
    return columns.map((column) => (
      <option key={column.name} value={column.name}>
        {column.name}
      </option>
    ));
  }

  function amountOptions() {
    return amountTables.flatMap((table) =>
      table.columns.map((column) => {
        const value = JSON.stringify({ table: table.name, column: column.name });
        return (
          <option key={value} value={value}>
            {table.name}.{column.name}
          </option>
        );
      }),
    );
  }

  function definitionPayload(): SalesDefinition {
    return {
      kind: "sales_margin_v1",
      fact_table: factTable,
      row_key: rowKey,
      order_id_column: orderId || null,
      date_column: dateColumn,
      date_format: dateFormat as SalesDefinition["date_format"],
      revenue_column: revenueColumn,
      refunds: refunds ? JSON.parse(refunds) : null,
      refunds_confirmed_absent: noRefunds,
      cost: cost ? JSON.parse(cost) : null,
      currency,
      currency_column: currencyColumn || null,
      single_currency_confirmed: singleCurrency,
      display_decimals: decimals,
      joins,
      filters,
      business_notes: notes,
    };
  }

  const signature = JSON.stringify({
    name,
    previousId,
    definition: definitionPayload(),
  });
  const approved = approvedSignature === signature;
  const canSave = approved && required && assumptions && joinsComplete && filtersComplete && !busy;

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    setBusy(true);
    try {
      const saved = await verifiedReports.create(dataset.id, {
        name,
        definition: definitionPayload(),
        approved: true,
        previous_id: previousId,
      });
      await queryClient.invalidateQueries({ queryKey: ["report-definitions", dataset.id] });
      setSelectedDefinition(saved.id);
      setPreviousId(null);
      setApprovedSignature(null);
      toast.success(`Saved definition version ${saved.version}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save report definition");
    } finally {
      setBusy(false);
    }
  }

  function editDefinition(value: ReportDefinition) {
    const definition = value.definition;
    setName(value.name);
    setFactTable(definition.fact_table);
    setRowKey(definition.row_key);
    setOrderId(definition.order_id_column ?? "");
    setDateColumn(definition.date_column);
    setDateFormat(definition.date_format);
    setRevenueColumn(definition.revenue_column);
    setRefunds(definition.refunds ? JSON.stringify(definition.refunds) : "");
    setCost(definition.cost ? JSON.stringify(definition.cost) : "");
    setCurrency(definition.currency);
    setCurrencyColumn(definition.currency_column ?? "");
    setDecimals(definition.display_decimals);
    setJoins(definition.joins ?? []);
    setFilters(definition.filters ?? []);
    setNotes(definition.business_notes ?? "");
    setNoRefunds(definition.refunds_confirmed_absent);
    setSingleCurrency(definition.single_currency_confirmed);
    setPreviousId(value.id);
    setApprovedSignature(null);
  }

  function removeJoin(index: number) {
    const table = joins[index]?.table;
    setJoins((items) => items.filter((_, position) => position !== index));
    if (table && refunds && JSON.parse(refunds).table === table) setRefunds("");
    if (table && cost && JSON.parse(cost).table === table) setCost("");
  }

  async function runReport() {
    if (!selectedDefinition || !selectedVersion || !periodStart || !periodEnd) return;
    setBusy(true);
    try {
      const run = await verifiedReports.run(dataset.id, selectedDefinition, {
        version_id: selectedVersion,
        start_date: periodStart,
        end_date: periodEnd,
      });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      router.push(`/sessions/${run.session_id}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not start report");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Verified spreadsheet report</CardTitle>
      </CardHeader>
      <CardContent className="space-y-8">
        <form aria-label="Approved report setup" className="space-y-4" onSubmit={save}>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <Label htmlFor="report-name">Report name</Label>
              <Input
                id="report-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="sales-table">Sales table</Label>
              <NativeSelect
                id="sales-table"
                value={factTable}
                onChange={(value) => {
                  setFactTable(value);
                  setRowKey("");
                  setOrderId("");
                  setDateColumn("");
                  setRevenueColumn("");
                  setRefunds("");
                  setCost("");
                  setCurrencyColumn("");
                  setJoins([]);
                  setFilters([]);
                  setNoRefunds(false);
                  setSingleCurrency(false);
                }}
              >
                <option value="">Select table</option>
                {tables.map((table) => (
                  <option key={table.name} value={table.name}>
                    {table.name}
                  </option>
                ))}
              </NativeSelect>
            </div>
            {[
              ["Unique row ID", "row-id", rowKey, setRowKey],
              ["Order ID (optional)", "order-id", orderId, setOrderId],
              ["Reporting date", "report-date", dateColumn, setDateColumn],
              ["Gross sales amount", "gross-sales", revenueColumn, setRevenueColumn],
            ].map(([label, id, value, setter]) => (
              <div key={String(id)}>
                <Label htmlFor={String(id)}>{String(label)}</Label>
                <NativeSelect
                  id={String(id)}
                  value={String(value)}
                  onChange={setter as (value: string) => void}
                >
                  <option value="">Select column</option>
                  {columnOptions()}
                </NativeSelect>
              </div>
            ))}
            <div>
              <Label htmlFor="date-format">Text date format</Label>
              <NativeSelect id="date-format" value={dateFormat} onChange={setDateFormat}>
                <option value="%Y-%m-%d">ISO %Y-%m-%d</option>
                <option value="%d/%m/%Y">Day-first %d/%m/%Y</option>
                <option value="%m/%d/%Y">Month-first %m/%d/%Y</option>
              </NativeSelect>
              <p className="text-xs text-muted-foreground">
                Use ISO for already imported DATE columns; preserve text during import to control
                ambiguous text dates.
              </p>
            </div>
            <div>
              <Label htmlFor="refund-amount">Refund amount</Label>
              <NativeSelect
                id="refund-amount"
                value={refunds}
                onChange={(value) => {
                  setRefunds(value);
                  if (value) setNoRefunds(false);
                }}
              >
                <option value="">No mapped refund amount</option>
                {amountOptions()}
              </NativeSelect>
              {!refunds && (
                <label className="mt-2 flex gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={noRefunds}
                    onChange={(event) => setNoRefunds(event.target.checked)}
                  />
                  This dataset has no refunds
                </label>
              )}
            </div>
            <div>
              <Label htmlFor="cost-amount">Total cost amount</Label>
              <NativeSelect id="cost-amount" value={cost} onChange={setCost}>
                <option value="">No mapped total cost</option>
                {amountOptions()}
              </NativeSelect>
            </div>
            <div>
              <Label htmlFor="currency">Currency</Label>
              <Input
                id="currency"
                maxLength={3}
                value={currency}
                onChange={(event) => setCurrency(event.target.value.toUpperCase())}
              />
            </div>
            <div>
              <Label htmlFor="currency-column">Currency column</Label>
              <NativeSelect
                id="currency-column"
                value={currencyColumn}
                onChange={(value) => {
                  setCurrencyColumn(value);
                  if (value) setSingleCurrency(false);
                }}
              >
                <option value="">No currency column</option>
                {columnOptions()}
              </NativeSelect>
              {!currencyColumn && (
                <label className="mt-2 flex gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={singleCurrency}
                    onChange={(event) => setSingleCurrency(event.target.checked)}
                  />
                  All amounts use the selected currency
                </label>
              )}
            </div>
            <div>
              <Label htmlFor="display-decimals">Display decimals</Label>
              <Input
                id="display-decimals"
                type="number"
                min={0}
                max={6}
                value={decimals}
                onChange={(event) => setDecimals(Number(event.target.value))}
              />
            </div>
          </div>
          <div>
            <Label htmlFor="business-notes">Business notes</Label>
            <Textarea
              id="business-notes"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          </div>
          <details className="space-y-4 rounded border p-3">
            <summary className="font-medium">Advanced joins and filters</summary>
            <p className="text-sm text-muted-foreground">
              Only one-to-one joins are supported. Keys must be unique and every source row must
              match. Cost/refund amounts must be totals per source row, not unit prices.
            </p>
            {joins.map((join, index) => {
              const lookup = tables.find((table) => table.name === join.table)?.columns ?? [];
              return (
                <div key={index} className="grid gap-2 rounded border p-2 md:grid-cols-4">
                  <Label className="sr-only" htmlFor={`join-table-${index}`}>
                    Join table
                  </Label>
                  <NativeSelect
                    id={`join-table-${index}`}
                    value={join.table}
                    onChange={(value) =>
                      setJoins((items) =>
                        items.map((item, position) =>
                          position === index ? { ...item, table: value, lookup_key: "" } : item,
                        ),
                      )
                    }
                  >
                    <option value="">Join table</option>
                    {tables
                      .filter(
                        (table) =>
                          table.name !== factTable &&
                          !joins.some(
                            (item, position) => position !== index && item.table === table.name,
                          ),
                      )
                      .map((table) => (
                        <option key={table.name} value={table.name}>
                          {table.name}
                        </option>
                      ))}
                  </NativeSelect>
                  <Label className="sr-only" htmlFor={`fact-key-${index}`}>
                    Fact key
                  </Label>
                  <NativeSelect
                    id={`fact-key-${index}`}
                    value={join.fact_key}
                    onChange={(value) =>
                      setJoins((items) =>
                        items.map((item, position) =>
                          position === index ? { ...item, fact_key: value } : item,
                        ),
                      )
                    }
                  >
                    <option value="">Fact key</option>
                    {columnOptions()}
                  </NativeSelect>
                  <Label className="sr-only" htmlFor={`lookup-key-${index}`}>
                    Lookup key
                  </Label>
                  <NativeSelect
                    id={`lookup-key-${index}`}
                    value={join.lookup_key}
                    onChange={(value) =>
                      setJoins((items) =>
                        items.map((item, position) =>
                          position === index ? { ...item, lookup_key: value } : item,
                        ),
                      )
                    }
                  >
                    <option value="">Lookup key</option>
                    {columnOptions(lookup)}
                  </NativeSelect>
                  <Button type="button" variant="ghost" onClick={() => removeJoin(index)}>
                    Remove join
                  </Button>
                </div>
              );
            })}
            <Button
              type="button"
              variant="outline"
              disabled={joins.length >= 2}
              onClick={() =>
                setJoins((items) => [
                  ...items,
                  { table: "", fact_key: "", lookup_key: "", cardinality: "one_to_one" },
                ])
              }
            >
              Add approved join
            </Button>
            {filters.map((filter, index) => (
              <div key={index} className="grid gap-2 rounded border p-2 md:grid-cols-4">
                <Label className="sr-only" htmlFor={`filter-column-${index}`}>
                  Filter column
                </Label>
                <NativeSelect
                  id={`filter-column-${index}`}
                  value={filter.column}
                  onChange={(value) =>
                    setFilters((items) =>
                      items.map((item, position) =>
                        position === index ? { ...item, column: value } : item,
                      ),
                    )
                  }
                >
                  <option value="">Filter column</option>
                  {columnOptions()}
                </NativeSelect>
                <Label className="sr-only" htmlFor={`filter-operator-${index}`}>
                  Filter operator
                </Label>
                <NativeSelect
                  id={`filter-operator-${index}`}
                  value={filter.operator}
                  onChange={(value) =>
                    setFilters((items) =>
                      items.map((item, position) =>
                        position === index
                          ? { ...item, operator: value as Filter["operator"] }
                          : item,
                      ),
                    )
                  }
                >
                  <option value="equals">equals</option>
                  <option value="not_equals">not_equals</option>
                </NativeSelect>
                <Label className="sr-only" htmlFor={`filter-value-${index}`}>
                  Filter value
                </Label>
                <Input
                  id={`filter-value-${index}`}
                  value={filter.value}
                  placeholder="Literal value"
                  onChange={(event) =>
                    setFilters((items) =>
                      items.map((item, position) =>
                        position === index ? { ...item, value: event.target.value } : item,
                      ),
                    )
                  }
                />
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() =>
                    setFilters((items) => items.filter((_, position) => position !== index))
                  }
                >
                  Remove filter
                </Button>
              </div>
            ))}
            <p className="text-xs text-muted-foreground">
              Filters use AND semantics. Null values do not match equals or not_equals filters.
            </p>
            <Button
              type="button"
              variant="outline"
              disabled={filters.length >= 5}
              onClick={() =>
                setFilters((items) => [...items, { column: "", operator: "equals", value: "" }])
              }
            >
              Add filter
            </Button>
          </details>
          <div className="rounded-md bg-muted p-3 text-sm">
            <p>Gross sales = sum(mapped sales amount)</p>
            <p>Net sales = gross sales − refunds</p>
            <p>Gross profit = net sales − total cost</p>
            <p>Margin % = 100 × gross profit / net sales</p>
            <p>order count = distinct mapped order ID (or row ID)</p>
          </div>
          <label className="flex gap-2 text-sm">
            <input
              type="checkbox"
              checked={approved}
              onChange={(event) => setApprovedSignature(event.target.checked ? signature : null)}
            />
            I approve these definitions and assumptions
          </label>
          <Button type="submit" disabled={!canSave}>
            Save approved report
          </Button>
        </form>
        <div className="space-y-4 border-t pt-6">
          <h3 className="font-medium">Run saved report</h3>
          <Label htmlFor="saved-report">Saved report</Label>
          <NativeSelect
            id="saved-report"
            value={selectedDefinition}
            onChange={setSelectedDefinition}
          >
            <option value="">Select saved report</option>
            {(definitions.data ?? []).map((definition) => (
              <option key={definition.id} value={definition.id}>
                {definition.name} · v{definition.version}
              </option>
            ))}
          </NativeSelect>
          {selectedDefinition && (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                const selected = definitions.data?.find(
                  (definition) => definition.id === selectedDefinition,
                );
                if (selected) editDefinition(selected);
              }}
            >
              Edit as new version
            </Button>
          )}
          <Label htmlFor="report-version">Dataset version</Label>
          <NativeSelect id="report-version" value={selectedVersion} onChange={setSelectedVersion}>
            <option value="">Select confirmed version</option>
            {readyVersions.map((version) => (
              <option key={version.id} value={version.id}>
                {version.id.slice(0, 8)}
                {version.id === dataset.current_version_id ? " · current" : ""}
              </option>
            ))}
          </NativeSelect>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <Label htmlFor="period-start">Period start</Label>
              <Input
                id="period-start"
                type="date"
                value={periodStart}
                onChange={(event) => setPeriodStart(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="period-end">Period end</Label>
              <Input
                id="period-end"
                type="date"
                value={periodEnd}
                onChange={(event) => setPeriodEnd(event.target.value)}
              />
            </div>
          </div>
          <Button
            type="button"
            disabled={!selectedDefinition || !selectedVersion || !periodStart || !periodEnd || busy}
            onClick={runReport}
          >
            Run saved report
          </Button>
          {selectedDefinition && (
            <details>
              <summary>Saved configuration</summary>
              <pre className="overflow-auto text-xs">
                {JSON.stringify(
                  definitions.data?.find((definition) => definition.id === selectedDefinition),
                  null,
                  2,
                )}
              </pre>
            </details>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
