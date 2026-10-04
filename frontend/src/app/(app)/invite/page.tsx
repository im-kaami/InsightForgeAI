"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { workspaces } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function InvitePage() {
  const router = useRouter();
  const client = useQueryClient();
  const { user } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function accept() {
    // The secret is after "#", so it never reaches a server log; it is posted, not put in a URL.
    const secret = window.location.hash.slice(1);
    if (!secret) {
      setError("This invitation link is incomplete.");
      return;
    }
    setBusy(true);
    try {
      const joined = await workspaces.accept(secret);
      await client.invalidateQueries({ queryKey: ["workspaces"] });
      await client.invalidateQueries({ queryKey: ["datasets"] });
      router.push(`/workspaces?joined=${joined.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The invitation could not be accepted");
      setBusy(false);
    }
  }

  return (
    <div className="max-w-xl space-y-4">
      <h1 className="text-3xl font-semibold">Join a workspace</h1>
      <p className="text-muted-foreground">
        You were invited to a workspace. You are signed in as <strong>{user?.email}</strong>; the
        invitation must be for this address.
      </p>
      {error ? (
        <p role="alert" data-testid="invite-error" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <Button type="button" onClick={accept} disabled={busy}>
        {busy ? "Joining..." : "Accept invitation"}
      </Button>
    </div>
  );
}
