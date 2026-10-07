"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { auth } from "@/lib/api";

export default function VerifyPage() {
  const started = useRef(false);
  const [state, setState] = useState<"working" | "done" | "failed">("working");

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    // The token travels after "#", so it never reaches a server log; take it out of the address bar.
    const token = decodeURIComponent(window.location.hash.slice(1));
    if (window.location.hash) window.history.replaceState(null, "", window.location.pathname);
    auth
      .verifyEmail(token)
      .then(() => setState("done"))
      .catch(() => setState("failed"));
  }, []);

  return (
    <main className="grid min-h-screen place-items-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Confirm your email address</CardTitle>
          <CardDescription>
            {state === "working" ? "Checking your link..." : "This link works for 48 hours."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4" data-testid={`verify-${state}`}>
          {state === "done" ? <p>Your email address is confirmed.</p> : null}
          {state === "failed" ? (
            <p role="alert" className="text-destructive">
              This confirmation link is invalid or has expired. Sign in to be sent a new one.
            </p>
          ) : null}
          {state !== "working" ? (
            <Link className="text-foreground underline" href="/login">
              Go to sign in
            </Link>
          ) : null}
        </CardContent>
      </Card>
    </main>
  );
}
