import { Badge } from "@/components/ui/badge";

export type CodeResult = {
  name: string;
  code: string;
  ok: boolean;
  stdout?: string;
  error?: string | null;
  columns?: string[];
  rows?: Record<string, unknown>[];
  total_rows?: number;
  value?: unknown;
  note?: string | null;
  limits?: Record<string, string>;
};

const cell = (value: unknown) =>
  typeof value === "number"
    ? value.toLocaleString(undefined, { maximumSignificantDigits: 6 })
    : String(value ?? "");

export function CodeResultCard({ result }: { result: CodeResult }) {
  const columns = result.columns ?? [];
  const rows = (result.rows ?? []).slice(0, 50);
  const limits = result.limits ?? {};
  return (
    <section data-testid="code-result" className="space-y-3 rounded-md border p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">{result.name}</h3>
        <Badge variant="outline">Free-form code (sandboxed)</Badge>
        <Badge variant={result.ok ? "secondary" : "destructive"}>
          {result.ok ? "Ran" : "Failed"}
        </Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        Written by the AI and run in an isolated container
        {limits.network ? ` (network ${limits.network}, ${limits.memory} memory, ` : " ("}
        {limits.timeout_seconds ? `${limits.timeout_seconds} s limit)` : "limited resources)"}.
        Check it before relying on it; tested methods are more trustworthy.
      </p>
      <pre className="max-h-64 overflow-auto rounded bg-muted p-2 text-xs">
        <code>{result.code}</code>
      </pre>
      {result.error && (
        <pre
          role="alert"
          className="overflow-auto rounded border border-destructive/30 p-2 text-xs"
        >
          {result.error}
        </pre>
      )}
      {result.stdout && (
        <pre className="max-h-40 overflow-auto rounded border p-2 text-xs">{result.stdout}</pre>
      )}
      {result.value !== undefined && result.value !== null && (
        <p>
          Result: <span className="font-medium">{cell(result.value)}</span>
        </p>
      )}
      {columns.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column} className="py-1 pr-3 font-normal text-muted-foreground">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index} className="border-t">
                  {columns.map((column) => (
                    <td key={column} className="py-1 pr-3">
                      {cell(row[column])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {(result.total_rows ?? 0) > rows.length && (
            <p className="text-xs text-muted-foreground">
              Showing {rows.length} of {result.total_rows} rows.
            </p>
          )}
        </div>
      )}
      {result.note && <p className="text-xs text-muted-foreground">{result.note}</p>}
    </section>
  );
}
