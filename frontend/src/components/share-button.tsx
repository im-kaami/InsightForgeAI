"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { shares, shareUrl } from "@/lib/api";

export function ShareButton({ kind, targetId }: { kind: "dashboard" | "run"; targetId: string }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [days, setDays] = useState("7");
  const [agreed, setAgreed] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function create() {
    if (!agreed || busy) return;
    setBusy(true);
    try {
      const created = await shares.create(kind, targetId, Number(days));
      setLink(shareUrl(created.secret));
      await client.invalidateQueries({ queryKey: ["shares"] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not create the link");
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      toast.success("Link copied");
    } catch {
      toast.error("Could not copy; select the link and copy it by hand");
    }
  }

  if (!open)
    return (
      <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
        Share
      </Button>
    );
  return (
    <div
      data-testid="share-panel"
      className="w-full max-w-xl space-y-2 rounded-md border p-3 text-sm"
    >
      {link ? (
        <>
          <p className="font-medium">
            Copy this link now; it is shown only once. Anyone with it can view this{" "}
            {kind === "dashboard" ? "dashboard" : "answer"} until it expires or you revoke it on the
            Sharing page.
          </p>
          <code className="block break-all rounded bg-muted px-2 py-1" data-testid="share-url">
            {link}
          </code>
          <div className="flex gap-2">
            <Button type="button" size="sm" variant="outline" onClick={copy}>
              Copy link
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Done
            </Button>
          </div>
        </>
      ) : (
        <>
          <label className="flex items-center gap-2">
            <span>Expires after</span>
            <select
              aria-label="Link expires after"
              className="h-8 rounded-md border bg-background px-2"
              value={days}
              onChange={(event) => setDays(event.target.value)}
            >
              {["1", "7", "30", "90"].map((value) => (
                <option key={value} value={value}>
                  {value} day{value === "1" ? "" : "s"}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(event) => setAgreed(event.target.checked)}
            />
            <span>
              I understand that anyone with the link can see the{" "}
              {kind === "dashboard" ? "tiles" : "summary, tables and charts"}, including the data in
              them, without signing in.
            </span>
          </label>
          <div className="flex gap-2">
            <Button type="button" size="sm" onClick={create} disabled={!agreed || busy}>
              Create link
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
