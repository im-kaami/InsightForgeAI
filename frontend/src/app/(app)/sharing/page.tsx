"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { shares, type ShareLink } from "@/lib/api";

const day = (value: string | null | undefined) => (value ? value.slice(0, 10) : "never");

function state(link: ShareLink) {
  if (link.revoked_at) return "Revoked";
  return new Date(link.expires_at) <= new Date() ? "Expired" : "Active";
}

export default function SharingPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["shares"], queryFn: shares.list });
  const [busy, setBusy] = useState(false);

  async function revoke(link: ShareLink) {
    setBusy(true);
    try {
      await shares.revoke(link.id);
      await client.invalidateQueries({ queryKey: ["shares"] });
      toast.success("Link revoked");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not revoke the link");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-5xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold">Sharing</h1>
        <p className="text-muted-foreground">
          Read-only links to a dashboard or an answer. Anyone with a link can view it without
          signing in until it expires; revoke it here to stop it at once. Create links with
          &quot;Share&quot; on a dashboard or an answer. Links are shown only once.
        </p>
      </div>
      {query.data && query.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No links yet.</p>
      ) : (
        <Table data-testid="share-list">
          <TableHeader>
            <TableRow>
              <TableHead>Shows</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Expires</TableHead>
              <TableHead>Views</TableHead>
              <TableHead>Last viewed</TableHead>
              <TableHead>Status</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(query.data ?? []).map((link) => (
              <TableRow key={link.id}>
                <TableCell>{link.title}</TableCell>
                <TableCell>{link.kind === "dashboard" ? "Dashboard" : "Answer"}</TableCell>
                <TableCell>{day(link.created_at)}</TableCell>
                <TableCell>{day(link.expires_at)}</TableCell>
                <TableCell>{link.view_count}</TableCell>
                <TableCell>{day(link.last_viewed_at)}</TableCell>
                <TableCell>
                  <Badge variant={state(link) === "Active" ? "secondary" : "outline"}>
                    {state(link)}
                  </Badge>
                </TableCell>
                <TableCell>
                  {state(link) === "Active" ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Revoke link to ${link.title}`}
                      disabled={busy}
                      onClick={() => revoke(link)}
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
    </div>
  );
}
