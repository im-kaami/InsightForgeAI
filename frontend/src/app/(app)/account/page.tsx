"use client";

import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { auth } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function AccountPage() {
  const { user, loginWithToken, setUser, logout } = useAuth();
  const features = useQuery({ queryKey: ["auth-features"], queryFn: auth.features });
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  async function changePassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const next = String(data.get("new"));
    if (next.length < 8) return setError("The new password must be at least 8 characters");
    if (next !== String(data.get("confirm"))) return setError("The two new passwords do not match");
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await auth.changePassword(String(data.get("current")), next);
      await loginWithToken(result.access_token);
      form.reset();
      toast.success("Password changed; other sessions were signed out");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The password could not be changed");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function toggleAlerts(enabled: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      setUser(await auth.setEmailAlerts(enabled));
      toast.success(enabled ? "Email alerts are on" : "Email alerts are off");
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "Could not save the setting");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function sendTest() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await auth.testEmail();
      toast.success(`Test email sent to ${user?.email}`);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "Could not send the test email");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function signOutEverywhere() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await auth.logoutAll();
      setConfirming(false);
      logout();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "Could not sign out everywhere");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="max-w-xl space-y-6">
      <header>
        <h1 className="text-3xl font-semibold">Account</h1>
        <p className="text-muted-foreground" data-testid="account-email">
          {user?.email}
        </p>
      </header>
      <Card>
        <CardHeader>
          <CardTitle>Change password</CardTitle>
          <CardDescription>
            Other browsers and devices are signed out; this one stays signed in.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={changePassword} noValidate>
            <div className="space-y-2">
              <Label htmlFor="current">Current password</Label>
              <Input id="current" name="current" type="password" required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="new">New password</Label>
              <Input id="new" name="new" type="password" minLength={8} required />
              <p className="text-xs text-muted-foreground">At least 8 characters</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm">Repeat the new password</Label>
              <Input id="confirm" name="confirm" type="password" minLength={8} required />
            </div>
            {error ? (
              <p role="alert" data-testid="account-error" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}
            <Button type="submit" disabled={busy}>
              {busy ? "Saving..." : "Change password"}
            </Button>
          </form>
        </CardContent>
      </Card>
      <Card data-testid="email-alerts">
        <CardHeader>
          <CardTitle>Email alerts</CardTitle>
          <CardDescription>
            Emails contain the names you chose and a link, never data values.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {features.data && !features.data.email ? (
            <p className="text-sm text-muted-foreground" data-testid="email-off">
              Email is not set up on this server (SMTP settings in backend/.env).
            </p>
          ) : null}
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              className="size-4"
              checked={user?.email_alerts ?? false}
              disabled={!features.data?.email || busy}
              onChange={(event) => toggleAlerts(event.target.checked)}
            />
            Email me when a followed metric or a saved model raises an alert, or a scheduled
            analysis finishes
          </label>
          <Button variant="outline" onClick={sendTest} disabled={!features.data?.email || busy}>
            Send test email
          </Button>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Sign out everywhere</CardTitle>
          <CardDescription>
            Ends every session of this account, including this one. API tokens keep working.
            Signed-in browsers stay signed in for up to 30 days of inactivity.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" onClick={() => setConfirming(true)} disabled={busy}>
            Sign out everywhere
          </Button>
        </CardContent>
      </Card>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Sign out everywhere?</DialogTitle>
            <DialogDescription>
              Every browser and device signed in to this account, including this one, will need to
              sign in again.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirming(false)} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={signOutEverywhere} disabled={busy}>
              {busy ? "Signing out..." : "Sign out everywhere"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
