"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  datasets,
  type AppliedRecipe,
  type Dataset,
  type DatasetVersion,
  type RecipeStep,
  type ValidationReport,
  type ValidationRule,
} from "@/lib/api";

const STEP_KINDS: { value: RecipeStep["kind"]; label: string }[] = [
  { value: "rename_column", label: "Rename a column" },
  { value: "change_type", label: "Change a column type" },
  { value: "clean_text", label: "Trim or change the case of text" },
  { value: "map_values", label: "Replace values" },
  { value: "fill_missing", label: "Fill missing values" },
  { value: "drop_missing", label: "Remove rows with a missing value" },
  { value: "drop_duplicates", label: "Remove duplicate rows" },
  { value: "filter_rows", label: "Keep or remove rows" },
  { value: "derive_column", label: "Add a calculated column" },
];

const RULE_KINDS: { value: ValidationRule["kind"]; label: string }[] = [
  { value: "not_null", label: "Never missing" },
  { value: "unique", label: "No repeated values" },
  { value: "range", label: "Number range" },
  { value: "allowed_values", label: "Allowed values" },
  { value: "pattern", label: "Matches a pattern" },
  { value: "freshness", label: "Latest date is recent" },
  { value: "row_count", label: "Row count range" },
];

const OPERATORS = [
  "equals",
  "not_equals",
  "greater_than",
  "less_than",
  "at_least",
  "at_most",
  "is_missing",
  "is_not_missing",
] as const;

type Form = Record<string, string | boolean>;

const selectClass = "h-9 w-full rounded-md border bg-background px-3 text-sm";
const inputClass = "h-9 w-full rounded-md border bg-background px-3 text-sm";

function quote(value: unknown) {
  const text = String(value);
  return `'${text.length > 40 ? `${text.slice(0, 40)}...` : text}'`;
}

function human(value: string) {
  return value.replaceAll("_", " ");
}

export function describeStep(step: RecipeStep): string {
  const where = step.table;
  switch (step.kind) {
    case "rename_column":
      return `Rename ${where}.${step.column} to ${step.new_name}`;
    case "change_type":
      return `Change ${where}.${step.column} to ${step.to}${
        step.date_format ? ` using ${step.date_format}` : ""
      }${step.on_error === "empty" ? "; leave values that do not convert empty" : ""}`;
    case "clean_text": {
      const parts = [
        step.trim !== false && "trim spaces",
        step.collapse_spaces && "collapse repeated spaces",
        step.case && step.case !== "keep" && `make ${step.case} case`,
      ].filter(Boolean);
      return `Clean text in ${where}.${step.column}: ${parts.join(", ") || "no change"}`;
    }
    case "map_values":
      return `Replace values in ${where}.${step.column}${step.ignore_case ? " (ignoring case)" : ""}: ${step.mapping
        .slice(0, 3)
        .map(
          (item) =>
            `${quote(item.from_value)} -> ${item.to_value == null ? "missing" : quote(item.to_value)}`,
        )
        .join(", ")}${step.mapping.length > 3 ? ` and ${step.mapping.length - 3} more` : ""}`;
    case "fill_missing":
      return `Fill missing ${where}.${step.column} with ${
        step.method === "value" || !step.method
          ? quote(step.value)
          : `the ${human(step.method)} value`
      }`;
    case "drop_missing":
      return `Remove rows of ${where} where ${step.columns.join(" or ")} is missing`;
    case "drop_duplicates":
      return `Remove rows of ${where}${
        step.columns?.length ? ` with the same ${step.columns.join(", ")}` : " that repeat exactly"
      }, keeping the first`;
    case "filter_rows":
      return `${step.action === "keep" ? "Keep" : "Remove"} rows of ${where} where ${step.conditions
        .map(
          (item) =>
            `${item.column} ${human(item.op)}${item.value == null ? "" : ` ${quote(item.value)}`}`,
        )
        .join(" and ")}`;
    case "derive_column":
      return `Add column ${where}.${step.new_name} = ${step.expression}`;
  }
}

export function describeRule(rule: ValidationRule): string {
  const where = "column" in rule ? `${rule.table}.${rule.column}` : rule.table;
  switch (rule.kind) {
    case "not_null":
      return `${where} is never missing`;
    case "unique":
      return `${where} has no repeated values`;
    case "range":
      return `${where} is ${
        rule.min != null && rule.max != null
          ? `between ${rule.min} and ${rule.max}`
          : rule.min != null
            ? `at least ${rule.min}`
            : `at most ${rule.max}`
      }`;
    case "allowed_values":
      return `${where} is one of: ${rule.values.join(", ")}`;
    case "pattern":
      return `${where} matches ${rule.pattern}`;
    case "freshness":
      return `The latest date in ${where} is at most ${rule.max_age_days} days old`;
    case "row_count":
      return `${where} has ${
        rule.min != null && rule.max != null
          ? `between ${rule.min} and ${rule.max}`
          : rule.min != null
            ? `at least ${rule.min}`
            : `at most ${rule.max}`
      } rows`;
  }
}

function optionalNumber(value: string | boolean | undefined) {
  return typeof value === "string" && value.trim() !== "" ? Number(value) : null;
}

function list(value: string | boolean | undefined) {
  return typeof value === "string"
    ? value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
    : [];
}

function text(form: Form, key: string) {
  const value = form[key];
  return typeof value === "string" ? value : "";
}

function buildStep(kind: RecipeStep["kind"], form: Form): RecipeStep {
  const table = text(form, "table");
  const column = text(form, "column");
  switch (kind) {
    case "rename_column":
      return { kind, table, column, new_name: text(form, "new_name") };
    case "change_type":
      return {
        kind,
        table,
        column,
        to: (text(form, "to") || "number") as "number",
        date_format: text(form, "date_format") || null,
        on_error: form.on_error_empty ? "empty" : "fail",
      };
    case "clean_text":
      return {
        kind,
        table,
        column,
        trim: form.trim !== false,
        collapse_spaces: Boolean(form.collapse_spaces),
        case: (text(form, "case") || "keep") as "keep",
      };
    case "map_values":
      return {
        kind,
        table,
        column,
        ignore_case: Boolean(form.ignore_case),
        mapping: text(form, "mapping")
          .split("\n")
          .filter((line) => line.includes("=>"))
          .map((line) => {
            const [from, ...rest] = line.split("=>");
            const to = rest.join("=>").trim();
            return { from_value: from.trim(), to_value: to === "" ? null : to };
          }),
      };
    case "fill_missing":
      return {
        kind,
        table,
        column,
        method: (text(form, "method") || "value") as "value",
        value: text(form, "value") || null,
      };
    case "drop_missing":
      return { kind, table, columns: [column] };
    case "drop_duplicates":
      return { kind, table, columns: list(form.columns) };
    case "filter_rows":
      return {
        kind,
        table,
        action: (text(form, "action") || "remove") as "remove",
        conditions: [
          {
            column,
            op: (text(form, "op") || "equals") as "equals",
            value: text(form, "value") || null,
          },
        ],
      };
    case "derive_column":
      return {
        kind,
        table,
        new_name: text(form, "new_name"),
        expression: text(form, "expression"),
      };
  }
}

function buildRule(kind: ValidationRule["kind"], form: Form): ValidationRule {
  const table = text(form, "table");
  const column = text(form, "column");
  const severity = form.blocking ? "blocking" : "warning";
  switch (kind) {
    case "not_null":
    case "unique":
      return { kind, table, column, severity };
    case "range":
      return {
        kind,
        table,
        column,
        severity,
        min: optionalNumber(form.min),
        max: optionalNumber(form.max),
      };
    case "allowed_values":
      return {
        kind,
        table,
        column,
        severity,
        values: list(form.values),
        ignore_case: Boolean(form.ignore_case),
      };
    case "pattern":
      return { kind, table, column, severity, pattern: text(form, "pattern") };
    case "freshness":
      return { kind, table, column, severity, max_age_days: Number(text(form, "days") || "0") };
    case "row_count":
      return {
        kind,
        table,
        severity,
        min: optionalNumber(form.min),
        max: optionalNumber(form.max),
      };
  }
}

function move<T>(items: T[], index: number, offset: number) {
  const target = index + offset;
  if (target < 0 || target >= items.length) return items;
  const next = [...items];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}

function TableColumnFields({
  prefix,
  dataset,
  form,
  setForm,
  showColumn,
}: {
  prefix: string;
  dataset: Dataset;
  form: Form;
  setForm: (form: Form) => void;
  showColumn: boolean;
}) {
  const tables = dataset.schema?.tables ?? [];
  const table = tables.find((item) => item.name === text(form, "table")) ?? tables[0];
  const columns = table?.columns ?? [];
  return (
    <>
      <Field id={`${prefix}-table`} label="Table">
        <select
          id={`${prefix}-table`}
          className={selectClass}
          value={table?.name ?? ""}
          onChange={(event) => setForm({ ...form, table: event.target.value, column: "" })}
        >
          {tables.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </Field>
      {showColumn && (
        <Field id={`${prefix}-column`} label="Column">
          <select
            id={`${prefix}-column`}
            className={selectClass}
            value={text(form, "column") || columns[0]?.name || ""}
            onChange={(event) => setForm({ ...form, column: event.target.value })}
          >
            {columns.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>
      )}
    </>
  );
}

function withDefaults(dataset: Dataset, form: Form): Form {
  const tables = dataset.schema?.tables ?? [];
  const table = tables.find((item) => item.name === text(form, "table")) ?? tables[0];
  return {
    ...form,
    table: table?.name ?? "",
    column: text(form, "column") || table?.columns[0]?.name || "",
  };
}

export function RecipeSummary({ recipe }: { recipe?: AppliedRecipe | null }) {
  if (!recipe) return null;
  return (
    <div className="space-y-2 rounded-md border p-3 text-sm" data-testid="recipe-summary">
      <h3 className="font-medium">Cleaning recipe (revision {recipe.revision})</h3>
      {recipe.error ? (
        <p role="alert" className="text-destructive">
          The saved recipe could not be applied, so this version keeps the imported data unchanged.{" "}
          {recipe.error}
        </p>
      ) : (
        <ol className="list-decimal space-y-0.5 pl-5">
          {(recipe.results ?? []).map((result) => (
            <li key={result.index}>
              {result.description}
              <span className="text-muted-foreground">
                {" "}
                · rows {result.rows_before.toLocaleString()} to {result.rows_after.toLocaleString()}
                {result.changed_values != null &&
                  ` · ${result.changed_values.toLocaleString()} values changed`}
                {result.failed_values
                  ? ` · ${result.failed_values.toLocaleString()} values left empty`
                  : ""}
              </span>
            </li>
          ))}
        </ol>
      )}
      <p className="text-xs text-muted-foreground">{recipe.method}</p>
    </div>
  );
}

export function ValidationSummary({ report }: { report?: ValidationReport | null }) {
  if (!report) return null;
  const failed = (report.failed_blocking ?? 0) + (report.failed_warning ?? 0);
  return (
    <div className="space-y-2 rounded-md border p-3 text-sm" data-testid="validation-summary">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">Validation rules</h3>
        {report.failed_blocking ? (
          <Badge variant="destructive">{report.failed_blocking} blocking</Badge>
        ) : null}
        {report.failed_warning ? (
          <Badge variant="outline">{report.failed_warning} warning</Badge>
        ) : null}
        <Badge variant="secondary">{report.passed ?? 0} passed</Badge>
      </div>
      {report.failed_blocking ? (
        <p className="text-destructive">
          Verified reports on this version are blocked until the blocking rules pass.
        </p>
      ) : failed ? (
        <p className="text-muted-foreground">Verified reports on this version need review.</p>
      ) : null}
      <ul className="space-y-1">
        {(report.results ?? []).map((result) => (
          <li key={result.rule_id} className="flex flex-wrap gap-2">
            <Badge variant={result.status === "passed" ? "secondary" : "outline"}>
              {result.status === "passed"
                ? "Passed"
                : result.status === "error"
                  ? "Error"
                  : "Failed"}
            </Badge>
            <span>{result.description}</span>
            <span className="text-muted-foreground">
              {result.message}
              {(result.examples ?? []).length > 0 &&
                ` (for example ${result.examples?.join(", ")})`}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">{report.method}</p>
    </div>
  );
}

export function DatasetCleaning({
  dataset,
  currentVersion,
  onDraft,
}: {
  dataset: Dataset;
  currentVersion?: DatasetVersion;
  onDraft: (version: DatasetVersion) => void;
}) {
  const queryClient = useQueryClient();
  const [steps, setSteps] = useState<RecipeStep[]>(dataset.recipe?.steps ?? []);
  const [autoApply, setAutoApply] = useState(dataset.recipe?.auto_apply ?? true);
  const [rules, setRules] = useState<ValidationRule[]>(dataset.rules?.rules ?? []);
  const [stepKind, setStepKind] = useState<RecipeStep["kind"]>("clean_text");
  const [stepForm, setStepForm] = useState<Form>({});
  const [ruleKind, setRuleKind] = useState<ValidationRule["kind"]>("not_null");
  const [ruleForm, setRuleForm] = useState<Form>({});
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const suggestions = useQuery({
    queryKey: ["suggestions", dataset.id, dataset.current_version_id],
    queryFn: () => datasets.suggestions(dataset.id),
    enabled: showSuggestions,
  });
  const savedSteps = JSON.stringify(dataset.recipe?.steps ?? []);
  const recipeDirty =
    JSON.stringify(steps) !== savedSteps || autoApply !== (dataset.recipe?.auto_apply ?? true);
  const rulesDirty = JSON.stringify(rules) !== JSON.stringify(dataset.rules?.rules ?? []);
  const stepValues = withDefaults(dataset, stepForm);
  const ruleValues = withDefaults(dataset, ruleForm);

  async function run(action: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await action();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Something went wrong");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["dataset", dataset.id] });
    await queryClient.invalidateQueries({ queryKey: ["dataset-versions", dataset.id] });
  };

  const saveRecipe = () =>
    run(async () => {
      const saved = await datasets.saveRecipe(dataset.id, { steps, auto_apply: autoApply });
      setSteps(saved.recipe?.steps ?? []);
      await refresh();
      toast.success("Recipe saved");
    });

  const applyRecipe = () =>
    run(async () => {
      const draft = await datasets.applyRecipe(dataset.id);
      await refresh();
      onDraft(draft);
    });

  const saveRules = () =>
    run(async () => {
      const saved = await datasets.saveRules(dataset.id, { rules });
      setRules(saved.rules?.rules ?? []);
      await refresh();
      toast.success("Rules saved and checked");
    });

  const stepFields = (() => {
    const id = "step";
    const input = (key: string, label: string, placeholder?: string) => (
      <Field id={`${id}-${key}`} label={label}>
        <input
          id={`${id}-${key}`}
          className={inputClass}
          placeholder={placeholder}
          value={text(stepForm, key)}
          onChange={(event) => setStepForm({ ...stepForm, [key]: event.target.value })}
        />
      </Field>
    );
    const check = (key: string, label: string, fallback = false) => (
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={typeof stepForm[key] === "boolean" ? Boolean(stepForm[key]) : fallback}
          onChange={(event) => setStepForm({ ...stepForm, [key]: event.target.checked })}
        />
        {label}
      </label>
    );
    const choose = (key: string, label: string, options: readonly string[]) => (
      <Field id={`${id}-${key}`} label={label}>
        <select
          id={`${id}-${key}`}
          className={selectClass}
          value={text(stepForm, key) || options[0]}
          onChange={(event) => setStepForm({ ...stepForm, [key]: event.target.value })}
        >
          {options.map((option) => (
            <option key={option} value={option}>
              {human(option)}
            </option>
          ))}
        </select>
      </Field>
    );
    switch (stepKind) {
      case "rename_column":
        return input("new_name", "New name", "for example order_date");
      case "change_type":
        return (
          <>
            {choose("to", "New type", [
              "number",
              "integer",
              "text",
              "date",
              "timestamp",
              "boolean",
            ])}
            {input("date_format", "Date format (optional)", "for example %d/%m/%Y")}
            {check("on_error_empty", "Leave values that do not convert empty instead of stopping")}
          </>
        );
      case "clean_text":
        return (
          <>
            {check("trim", "Trim spaces at the start and end", true)}
            {check("collapse_spaces", "Collapse repeated spaces")}
            {choose("case", "Letter case", ["keep", "lower", "upper"])}
          </>
        );
      case "map_values":
        return (
          <>
            <Field id="step-mapping" label="Replacements, one per line (old => new)">
              <textarea
                id="step-mapping"
                className="min-h-20 w-full rounded-md border bg-background px-3 py-2 text-sm"
                placeholder={"remote => Remote\nwfh => Remote"}
                value={text(stepForm, "mapping")}
                onChange={(event) => setStepForm({ ...stepForm, mapping: event.target.value })}
              />
            </Field>
            {check("ignore_case", "Ignore capital letters when matching")}
          </>
        );
      case "fill_missing":
        return (
          <>
            {choose("method", "Fill with", ["value", "mean", "median", "most_common"])}
            {(text(stepForm, "method") || "value") === "value" && input("value", "Value")}
          </>
        );
      case "drop_duplicates":
        return input("columns", "Key columns (optional, comma-separated)", "empty = all columns");
      case "filter_rows":
        return (
          <>
            {choose("action", "Action", ["remove", "keep"])}
            {choose("op", "Condition", OPERATORS)}
            {!["is_missing", "is_not_missing"].includes(text(stepForm, "op")) &&
              input("value", "Value")}
          </>
        );
      case "derive_column":
        return (
          <>
            {input("new_name", "New column name", "for example revenue")}
            {input("expression", "Expression", "for example price * quantity")}
          </>
        );
      default:
        return null;
    }
  })();

  const ruleFields = (() => {
    const input = (key: string, label: string, placeholder?: string) => (
      <Field id={`rule-${key}`} label={label}>
        <input
          id={`rule-${key}`}
          className={inputClass}
          placeholder={placeholder}
          value={text(ruleForm, key)}
          onChange={(event) => setRuleForm({ ...ruleForm, [key]: event.target.value })}
        />
      </Field>
    );
    switch (ruleKind) {
      case "range":
      case "row_count":
        return (
          <>
            {input("min", "Lowest (optional)")}
            {input("max", "Highest (optional)")}
          </>
        );
      case "allowed_values":
        return input("values", "Allowed values (comma-separated)", "paid, refunded");
      case "pattern":
        return input("pattern", "Regular expression (whole value)", "[A-Z]{2}[0-9]+");
      case "freshness":
        return input("days", "Maximum age in days", "7");
      default:
        return null;
    }
  })();

  const recipeSuggestions = (suggestions.data?.recipe_steps ?? []).filter(
    (item) => !steps.some((step) => JSON.stringify(step) === JSON.stringify(item.step)),
  );
  const ruleSuggestions = (suggestions.data?.rules ?? []).filter(
    (item) => !rules.some((rule) => describeRule(rule) === describeRule(item.rule)),
  );

  return (
    <>
      <Card data-testid="cleaning-recipe">
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Cleaning recipe</CardTitle>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setShowSuggestions((value) => !value)}
          >
            {showSuggestions ? "Hide suggestions" : "Suggest from health check"}
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Steps run in order on a copy of the imported data. Applying them creates a draft version
            that you review and confirm; the original import is kept. Suggestions come from fixed
            checks on the health check, not from an AI model.
          </p>
          {showSuggestions && (
            <div className="space-y-2 rounded-md border p-3 text-sm" data-testid="suggestions">
              <h3 className="font-medium">Suggested cleaning steps</h3>
              {suggestions.isLoading && <p className="text-muted-foreground">Loading...</p>}
              {suggestions.data && recipeSuggestions.length === 0 && (
                <p className="text-muted-foreground">No further suggestions.</p>
              )}
              {recipeSuggestions.map((item) => (
                <div key={JSON.stringify(item.step)} className="flex flex-wrap items-center gap-2">
                  <span>{describeStep(item.step)}</span>
                  <span className="text-muted-foreground">({item.reason})</span>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => setSteps((current) => [...current, item.step])}
                  >
                    Add step
                  </Button>
                </div>
              ))}
            </div>
          )}
          {steps.length === 0 ? (
            <p className="text-sm">No cleaning steps yet.</p>
          ) : (
            <ol className="space-y-1 text-sm" data-testid="recipe-steps">
              {steps.map((step, index) => (
                <li
                  key={`${index}-${JSON.stringify(step)}`}
                  className="flex flex-wrap items-center gap-2 rounded-md border p-2"
                >
                  <span className="font-mono text-xs">{index + 1}.</span>
                  <span className="flex-1">{describeStep(step)}</span>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Move step ${index + 1} up`}
                    disabled={index === 0}
                    onClick={() => setSteps((current) => move(current, index, -1))}
                  >
                    Up
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Move step ${index + 1} down`}
                    disabled={index === steps.length - 1}
                    onClick={() => setSteps((current) => move(current, index, 1))}
                  >
                    Down
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove step ${index + 1}`}
                    onClick={() =>
                      setSteps((current) => current.filter((_, item) => item !== index))
                    }
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ol>
          )}
          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm font-medium">Add a step</summary>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <Field id="step-kind" label="Step">
                <select
                  id="step-kind"
                  className={selectClass}
                  value={stepKind}
                  onChange={(event) => {
                    setStepKind(event.target.value as RecipeStep["kind"]);
                    setStepForm({ table: text(stepForm, "table") });
                  }}
                >
                  {STEP_KINDS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </Field>
              <TableColumnFields
                prefix="step"
                dataset={dataset}
                form={stepValues}
                setForm={setStepForm}
                showColumn={!["drop_duplicates", "derive_column"].includes(stepKind)}
              />
              {stepFields}
            </div>
            <Button
              type="button"
              className="mt-3"
              variant="outline"
              onClick={() => {
                setSteps((current) => [...current, buildStep(stepKind, stepValues)]);
                setStepForm({ table: text(stepValues, "table") });
              }}
            >
              Add to recipe
            </Button>
          </details>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={autoApply}
              onChange={(event) => setAutoApply(event.target.checked)}
            />
            Apply this recipe automatically to every new upload or refresh
          </label>
          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={busy || !recipeDirty} onClick={saveRecipe}>
              {busy ? "Working..." : "Save recipe"}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy || recipeDirty || steps.length === 0 || !dataset.current_version_id}
              onClick={applyRecipe}
            >
              Apply to current data as a draft
            </Button>
            {recipeDirty && (
              <span className="self-center text-xs text-muted-foreground">
                Save the recipe before applying it.
              </span>
            )}
          </div>
          <RecipeSummary recipe={currentVersion?.recipe} />
        </CardContent>
      </Card>
      <Card data-testid="validation-rules">
        <CardHeader>
          <CardTitle>Validation rules</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Rules are checked by code on every row of every new version and before each verified
            report. A failing blocking rule stops verified reports; a failing warning marks them as
            needing review.
          </p>
          {showSuggestions && (
            <div className="space-y-2 rounded-md border p-3 text-sm">
              <h3 className="font-medium">Suggested rules (the current data meets them)</h3>
              {suggestions.data && ruleSuggestions.length === 0 && (
                <p className="text-muted-foreground">No further suggestions.</p>
              )}
              {ruleSuggestions.map((item) => (
                <div key={describeRule(item.rule)} className="flex flex-wrap items-center gap-2">
                  <span>{describeRule(item.rule)}</span>
                  <span className="text-muted-foreground">({item.reason})</span>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => setRules((current) => [...current, item.rule])}
                  >
                    Add rule
                  </Button>
                </div>
              ))}
            </div>
          )}
          {rules.length === 0 ? (
            <p className="text-sm">No rules yet.</p>
          ) : (
            <ul className="space-y-1 text-sm" data-testid="rule-list">
              {rules.map((rule, index) => (
                <li
                  key={rule.id ?? `${index}-${describeRule(rule)}`}
                  className="flex flex-wrap items-center gap-2 rounded-md border p-2"
                >
                  <span className="flex-1">{describeRule(rule)}</span>
                  <select
                    aria-label={`Severity of ${describeRule(rule)}`}
                    className="h-8 rounded-md border bg-background px-2 text-sm"
                    value={rule.severity ?? "warning"}
                    onChange={(event) =>
                      setRules((current) =>
                        current.map((item, position) =>
                          position === index
                            ? { ...item, severity: event.target.value as "warning" | "blocking" }
                            : item,
                        ),
                      )
                    }
                  >
                    <option value="warning">Warning</option>
                    <option value="blocking">Blocking</option>
                  </select>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove rule ${describeRule(rule)}`}
                    onClick={() =>
                      setRules((current) => current.filter((_, item) => item !== index))
                    }
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm font-medium">Add a rule</summary>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <Field id="rule-kind" label="Rule">
                <select
                  id="rule-kind"
                  className={selectClass}
                  value={ruleKind}
                  onChange={(event) => {
                    setRuleKind(event.target.value as ValidationRule["kind"]);
                    setRuleForm({ table: text(ruleForm, "table") });
                  }}
                >
                  {RULE_KINDS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </Field>
              <TableColumnFields
                prefix="rule"
                dataset={dataset}
                form={ruleValues}
                setForm={setRuleForm}
                showColumn={ruleKind !== "row_count"}
              />
              {ruleFields}
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={Boolean(ruleForm.blocking)}
                  onChange={(event) => setRuleForm({ ...ruleForm, blocking: event.target.checked })}
                />
                Blocking (stops verified reports when it fails)
              </label>
            </div>
            <Button
              type="button"
              className="mt-3"
              variant="outline"
              onClick={() => {
                setRules((current) => [...current, buildRule(ruleKind, ruleValues)]);
                setRuleForm({ table: text(ruleValues, "table") });
              }}
            >
              Add to rules
            </Button>
          </details>
          <Button type="button" disabled={busy || !rulesDirty} onClick={saveRules}>
            {busy ? "Working..." : "Save and check rules"}
          </Button>
          <ValidationSummary report={currentVersion?.validation} />
        </CardContent>
      </Card>
    </>
  );
}
