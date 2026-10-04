"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiTokens, type ApiToken, type ApiTokenCreated } from "@/lib/api";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";
const day = (value: string | null | undefined) => (value ? value.slice(0, 10) : "never");

function state(token: ApiToken) {
  if (token.revoked_at) return "Revoked";
  return new Date(token.expires_at) <= new Date() ? "Expired" : "Active";
}

export default function ApiTokensPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["api-tokens"], queryFn: apiTokens.list });
  const [form, setForm] = useState({ name: "", scope: "read" as "read" | "ask", days: "90" });
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  async function act(action: () => Promise<unknown>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await action();
      await client.invalidateQueries({ queryKey: ["api-tokens"] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Something went wrong");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function create() {
    const days = Number(form.days);
    if (!form.name.trim()) {
      toast.error("Give the token a name, such as the script that will use it");
      return;
    }
    if (!Number.isInteger(days) || days < 1 || days > 365) {
      toast.error("Choose an expiry of 1 to 365 days");
      return;
    }
    void act(async () => {
      setCreated(
        await apiTokens.create({
          name: form.name.trim(),
          scope: form.scope,
          expires_in_days: days,
        }),
      );
      setForm({ ...form, name: "" });
    });
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Token copied");
    } catch {
      toast.error("Could not copy; select the token and copy it by hand");
    }
  }

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">API tokens</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Use InsightForge from scripts and other tools. Send a token as{" "}
          <code className="rounded bg-muted px-1">Authorization: Bearer ifk_...</code> to the same
          API the app uses. A <strong>read</strong> token can only read your datasets, sessions,
          runs and reports. An <strong>ask</strong> token can also start sessions and ask questions.
          No token can upload or delete data, change settings, or manage tokens.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Create a token</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3 text-sm">
          <label className="space-y-1">
            <span className="block text-xs text-muted-foreground">Name</span>
            <Input
              aria-label="Token name"
              placeholder="Weekly export script"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </label>
          <label className="space-y-1">
            <span className="block text-xs text-muted-foreground">Access</span>
            <select
              aria-label="Token access"
              className={selectClass}
              value={form.scope}
              onChange={(event) =>
                setForm({ ...form, scope: event.target.value as "read" | "ask" })
              }
            >
              <option value="read">Read only</option>
              <option value="ask">Read and ask questions</option>
            </select>
          </label>
          <label className="space-y-1">
            <span className="block text-xs text-muted-foreground">Expires after (days)</span>
            <Input
              aria-label="Expires after (days)"
              className="w-28"
              inputMode="numeric"
              value={form.days}
              onChange={(event) => setForm({ ...form, days: event.target.value })}
            />
          </label>
          <Button type="button" onClick={create} disabled={busy}>
            Create token
          </Button>
        </CardContent>
      </Card>
      {created && (
        <div
          role="status"
          data-testid="new-token"
          className="space-y-2 rounded-md border border-amber-500/40 p-4 text-sm"
        >
          <p className="font-medium">
            Copy this token now. It is shown only once; InsightForge keeps only a fingerprint of it.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="break-all rounded bg-muted px-2 py-1" data-testid="new-token-value">
              {created.token}
            </code>
            <Button type="button" size="sm" variant="outline" onClick={() => copy(created.token)}>
              Copy
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setCreated(null)}>
              Done
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Example: curl -H &quot;Authorization: Bearer {created.prefix}...&quot;{" "}
            {typeof window === "undefined" ? "" : window.location.origin}/api/datasets
          </p>
        </div>
      )}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Your tokens</CardTitle>
        </CardHeader>
        <CardContent>
          {query.data && query.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">No tokens yet.</p>
          ) : (
            <Table data-testid="token-list">
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Access</TableHead>
                  <TableHead>Starts with</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead>Expires</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {(query.data ?? []).map((token) => (
                  <TableRow key={token.id}>
                    <TableCell>{token.name}</TableCell>
                    <TableCell>{token.scope === "ask" ? "Read and ask" : "Read only"}</TableCell>
                    <TableCell>
                      <code>{token.prefix}...</code>
                    </TableCell>
                    <TableCell>{day(token.created_at)}</TableCell>
                    <TableCell>{day(token.last_used_at)}</TableCell>
                    <TableCell>{day(token.expires_at)}</TableCell>
                    <TableCell>
                      <Badge variant={state(token) === "Active" ? "secondary" : "outline"}>
                        {state(token)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {state(token) === "Active" ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          aria-label={`Revoke ${token.name}`}
                          disabled={busy}
                          onClick={() => act(() => apiTokens.revoke(token.id))}
                        >
                          Revoke
                        </Button>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
