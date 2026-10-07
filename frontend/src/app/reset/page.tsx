"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { auth } from "@/lib/api";

export default function ResetPage() {
  const secret = useRef("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    // The secret travels after "#", so it never reaches a server log. Keep it in memory and take it
    // out of the address bar straight away.
    secret.current = decodeURIComponent(window.location.hash.slice(1));
    if (window.location.hash) window.history.replaceState(null, "", window.location.pathname);
  }, []);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const password = String(data.get("password"));
    if (password.length < 8) return setError("Password must be at least 8 characters");
    if (password !== String(data.get("confirm"))) return setError("The two passwords do not match");
    setBusy(true);
    setError(null);
    try {
      await auth.resetPassword(secret.current, password);
      setDone(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The password could not be reset");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Choose a new password</CardTitle>
          <CardDescription>
            Everywhere you were signed in will be signed out. The link works once.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {done ? (
            <div className="space-y-4" data-testid="reset-done">
              <p>Your password was changed.</p>
              <Link className="text-foreground underline" href="/login">
                Sign in
              </Link>
            </div>
          ) : (
            <form className="space-y-4" onSubmit={submit} noValidate>
              <div className="space-y-2">
                <Label htmlFor="password">New password</Label>
                <Input id="password" name="password" type="password" minLength={8} required />
                <p className="text-xs text-muted-foreground">At least 8 characters</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="confirm">Repeat the new password</Label>
                <Input id="confirm" name="confirm" type="password" minLength={8} required />
              </div>
              {error ? (
                <p role="alert" data-testid="reset-error" className="text-sm text-destructive">
                  {error}
                </p>
              ) : null}
              <Button type="submit" className="w-full" disabled={busy}>
                {busy ? "Saving..." : "Set new password"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
