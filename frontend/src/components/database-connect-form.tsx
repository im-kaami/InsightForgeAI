"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { connections, type Connection } from "@/lib/api";

type DatabaseType = "postgres" | "postgres-cloud" | "mysql" | "mssql" | "sqlite" | "other";

const TYPES: { value: DatabaseType; label: string; port: string; scheme: string }[] = [
  { value: "postgres", label: "PostgreSQL", port: "5432", scheme: "postgresql" },
  {
    value: "postgres-cloud",
    label: "PostgreSQL in the cloud (Supabase, Neon; SSL required)",
    port: "5432",
    scheme: "postgresql",
  },
  { value: "mysql", label: "MySQL", port: "3306", scheme: "mysql" },
  { value: "mssql", label: "SQL Server", port: "1433", scheme: "mssql+pymssql" },
  { value: "sqlite", label: "SQLite file", port: "", scheme: "sqlite" },
  { value: "other", label: "Other (SQLAlchemy URI)", port: "", scheme: "" },
];

const field = "h-9 w-full rounded-md border bg-background px-3 text-sm";

export type DatabaseSelection = {
  connection_id?: string;
  uri?: string;
  name: string;
  tables?: string[];
  schema?: string;
};

function buildUri(
  type: DatabaseType,
  values: { host: string; port: string; database: string; user: string; password: string },
  path: string,
  raw: string,
) {
  if (type === "other") return raw.trim();
  if (type === "sqlite") return `sqlite:///${path.trim()}`;
  const spec = TYPES.find((item) => item.value === type)!;
  const login = values.user
    ? `${encodeURIComponent(values.user)}${
        values.password ? `:${encodeURIComponent(values.password)}` : ""
      }@`
    : "";
  const port = values.port || spec.port;
  const query = type === "postgres-cloud" ? "?sslmode=require" : "";
  return `${spec.scheme}://${login}${values.host.trim()}:${port}/${values.database.trim()}${query}`;
}

export function DatabaseConnectForm({
  busy,
  existing,
  onConnect,
}: {
  busy: boolean;
  existing: Connection[];
  onConnect: (value: DatabaseSelection) => void;
}) {
  const [type, setType] = useState<DatabaseType>("postgres");
  const [values, setValues] = useState({
    host: "",
    port: "",
    database: "",
    user: "",
    password: "",
  });
  const [path, setPath] = useState("");
  const [raw, setRaw] = useState("");
  const [schema, setSchema] = useState("");
  const [name, setName] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [found, setFound] = useState<string[] | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [testing, setTesting] = useState(false);
  const options = useQuery({ queryKey: ["connection-options"], queryFn: connections.options });
  const sqliteAllowed = options.data?.sqlite_files ?? false;
  const shownTypes = TYPES.filter((item) => item.value !== "sqlite" || sqliteAllowed);

  const usesSchema = type === "postgres" || type === "postgres-cloud" || type === "mssql";
  const hostBased = type !== "sqlite" && type !== "other";
  const uri = buildUri(type, values, path, raw);
  const complete = hostBased
    ? Boolean(values.host.trim() && values.database.trim())
    : type === "sqlite"
      ? Boolean(path.trim())
      : Boolean(raw.trim());
  const ready = Boolean(connectionId) || complete;
  const schemaValue = usesSchema && schema.trim() ? schema.trim() : undefined;

  function reset() {
    setFound(null);
    setChosen([]);
  }

  function update(change: Partial<typeof values>) {
    setValues((current) => ({ ...current, ...change }));
    reset();
  }

  async function listTables() {
    setTesting(true);
    try {
      const result = connectionId
        ? await connections.tables(connectionId, schemaValue)
        : await connections.test(uri, schemaValue);
      setFound(result.tables);
      setChosen(result.tables);
      if (result.tables.length === 0) toast.info("Connected, but no tables were found");
      else toast.success(`Connected: ${result.tables.length} tables found`);
    } catch (error) {
      reset();
      toast.error(error instanceof Error ? error.message : "Could not connect");
    } finally {
      setTesting(false);
    }
  }

  const allChosen = found !== null && chosen.length === found.length;

  return (
    <div className="space-y-4" data-testid="database-form">
      {existing.length > 0 && (
        <div className="space-y-2">
          <Label htmlFor="db-existing">Existing connection</Label>
          <select
            id="db-existing"
            className={field}
            value={connectionId}
            onChange={(event) => {
              setConnectionId(event.target.value);
              reset();
            }}
          >
            <option value="">New connection</option>
            {existing.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} ({item.redacted_uri})
              </option>
            ))}
          </select>
        </div>
      )}
      {!connectionId && (
        <>
          <div className="space-y-2">
            <Label htmlFor="db-type">Database type</Label>
            <select
              id="db-type"
              className={field}
              value={type}
              onChange={(event) => {
                setType(event.target.value as DatabaseType);
                reset();
              }}
            >
              {shownTypes.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          {hostBased && (
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2 space-y-1">
                <Label htmlFor="db-host">Host</Label>
                <Input
                  id="db-host"
                  value={values.host}
                  onChange={(event) => update({ host: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="db-port">Port</Label>
                <Input
                  id="db-port"
                  inputMode="numeric"
                  placeholder={TYPES.find((item) => item.value === type)?.port}
                  value={values.port}
                  onChange={(event) => update({ port: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="db-name">Database</Label>
                <Input
                  id="db-name"
                  value={values.database}
                  onChange={(event) => update({ database: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="db-user">User</Label>
                <Input
                  id="db-user"
                  autoComplete="off"
                  value={values.user}
                  onChange={(event) => update({ user: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="db-password">Password</Label>
                <Input
                  id="db-password"
                  type="password"
                  autoComplete="new-password"
                  value={values.password}
                  onChange={(event) => update({ password: event.target.value })}
                />
              </div>
            </div>
          )}
          {type === "sqlite" && (
            <div className="space-y-1">
              <Label htmlFor="db-path">Path to the SQLite file on the server</Label>
              <Input
                id="db-path"
                value={path}
                onChange={(event) => {
                  setPath(event.target.value);
                  reset();
                }}
              />
            </div>
          )}
          {type === "other" && (
            <div className="space-y-1">
              <Label htmlFor="db-uri">SQLAlchemy URI</Label>
              <Input
                id="db-uri"
                placeholder="dialect+driver://user:password@host:port/database"
                value={raw}
                onChange={(event) => {
                  setRaw(event.target.value);
                  reset();
                }}
              />
            </div>
          )}
        </>
      )}
      {usesSchema && (
        <div className="space-y-1">
          <Label htmlFor="db-schema">Schema (optional)</Label>
          <Input
            id="db-schema"
            placeholder={type === "mssql" ? "dbo" : "public"}
            value={schema}
            onChange={(event) => {
              setSchema(event.target.value);
              reset();
            }}
          />
        </div>
      )}
      <Button
        type="button"
        variant="outline"
        disabled={!ready || testing || busy}
        onClick={listTables}
      >
        {testing ? "Connecting..." : "Test connection"}
      </Button>
      {found !== null && (
        <div className="space-y-2" data-testid="database-tables">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium">
              Tables to use ({chosen.length} of {found.length})
            </span>
            <button
              type="button"
              className="text-xs underline"
              onClick={() => setChosen(allChosen ? [] : found)}
            >
              {allChosen ? "Clear all" : "Select all"}
            </button>
          </div>
          <div className="max-h-40 space-y-1 overflow-auto rounded border p-2">
            {found.map((table) => (
              <label key={table} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={chosen.includes(table)}
                  onChange={(event) =>
                    setChosen((current) =>
                      event.target.checked
                        ? [...current, table]
                        : current.filter((item) => item !== table),
                    )
                  }
                />
                {table}
              </label>
            ))}
          </div>
        </div>
      )}
      <Input
        placeholder="Dataset name"
        aria-label="Dataset name"
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <Button
        type="button"
        disabled={!ready || busy || (found !== null && chosen.length === 0)}
        onClick={() =>
          onConnect({
            connection_id: connectionId || undefined,
            uri: connectionId ? undefined : uri,
            name: name || "Connected data",
            tables: found !== null && !allChosen ? chosen : undefined,
            schema: schemaValue,
          })
        }
      >
        Connect
      </Button>
      <p className="text-xs text-muted-foreground">
        Nothing is written to your database. PostgreSQL, MySQL and SQLite are attached read-only;
        SQL Server and other databases are copied into a snapshot each time the data is opened.
      </p>
    </div>
  );
}
