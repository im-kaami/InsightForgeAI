"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { API_BASE, auth } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const credentialsSchema = z.object({
  email: z.string().email("Enter a valid email address"),
  password: z.string().min(8, "Password must be at least 8 characters"),
});

export default function LoginPage() {
  const { login, loginWithToken } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [sso, setSso] = useState<{ enabled: boolean; name: string } | null>(null);
  const [canEmail, setCanEmail] = useState(false);

  useEffect(() => {
    // After single sign-on the server returns here with the session token after "#", which never
    // reaches a server log. Remove it from the address bar straight away.
    const values = new URLSearchParams(window.location.hash.slice(1));
    const token = values.get("oidc_token");
    const problem = values.get("oidc_error");
    if (token || problem) window.history.replaceState(null, "", window.location.pathname);
    if (token)
      loginWithToken(token)
        .then(() => router.push("/datasets"))
        .catch(() => toast.error("Single sign-on did not complete; please try again"));
    else if (problem) toast.error(problem);
    fetch(`${API_BASE}/auth/oidc/config`)
      .then((response) => (response.ok ? response.json() : null))
      .then(setSso)
      .catch(() => setSso(null));
    auth
      .features()
      .then((features) => setCanEmail(features.email))
      .catch(() => setCanEmail(false));
    // Runs once, when the page opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const credentials = credentialsSchema.safeParse({
      email: String(data.get("email")),
      password: String(data.get("password")),
    });
    if (!credentials.success) {
      toast.error(credentials.error.issues[0].message);
      return;
    }
    setBusy(true);
    try {
      await login(credentials.data.email, credentials.data.password);
      router.push("/datasets");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="grid min-h-screen place-items-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Sign in to InsightForge</CardTitle>
          <CardDescription>Continue to your datasets and analysis sessions.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={submit} noValidate>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" name="email" type="email" required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input id="password" name="password" type="password" minLength={8} required />
              {canEmail ? (
                <Link className="text-xs text-muted-foreground underline" href="/forgot">
                  Forgot password?
                </Link>
              ) : null}
            </div>
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Signing in..." : "Sign in"}
            </Button>
            {sso?.enabled ? (
              <a
                href={`${API_BASE}/auth/oidc/start`}
                className="flex h-9 w-full items-center justify-center rounded-md border text-sm font-medium hover:bg-muted"
                data-testid="sso-button"
              >
                Sign in with {sso.name}
              </a>
            ) : null}
            <p className="text-center text-sm text-muted-foreground">
              New here?{" "}
              <Link className="text-foreground underline" href="/register">
                Create an account
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
