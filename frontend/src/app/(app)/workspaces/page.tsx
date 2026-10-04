"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { workspaces, type Workspace } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const ROLES = [
  { value: "viewer", label: "Viewer: read and ask" },
  { value: "editor", label: "Editor: also change definitions and data" },
  { value: "owner", label: "Owner: also manage members" },
] as const;
const selectClass = "h-8 rounded-md border bg-background px-2 text-sm";

function WorkspaceCard({ workspace }: { workspace: Workspace }) {
  const client = useQueryClient();
  const { user } = useAuth();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"viewer" | "editor" | "owner">("viewer");
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const owner = workspace.role === "owner";

  async function act(action: () => Promise<unknown>, done?: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await action();
      await client.invalidateQueries({ queryKey: ["workspaces"] });
      await client.invalidateQueries({ queryKey: ["datasets"] });
      await client.invalidateQueries({ queryKey: ["dashboards"] });
      if (done) toast.success(done);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Something went wrong");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <Card data-testid="workspace-card">
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div>
          <CardTitle className="text-base">{workspace.name}</CardTitle>
          <p className="text-xs text-muted-foreground">Your role: {workspace.role}</p>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => {
              if (
                !user ||
                !window.confirm(
                  `Leave ${workspace.name}? What you shared with it stops being shared.`,
                )
              )
                return;
              void act(
                () => workspaces.removeMember(workspace.id, user.id),
                "You left the workspace",
              );
            }}
          >
            Leave
          </Button>
          {owner ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => {
                if (
                  !window.confirm(
                    `Delete ${workspace.name}? Everything shared with it stops being shared.`,
                  )
                )
                  return;
                void act(() => workspaces.remove(workspace.id), "Workspace deleted");
              }}
            >
              Delete
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div>
          <h3 className="mb-1 font-medium">Members</h3>
          <ul className="space-y-1" data-testid="member-list">
            {(workspace.members ?? []).map((member) => (
              <li key={member.user_id} className="flex flex-wrap items-center gap-2">
                <span>{member.email}</span>
                {owner && member.user_id !== user?.id ? (
                  <>
                    <select
                      aria-label={`Role of ${member.email}`}
                      className={selectClass}
                      value={member.role}
                      disabled={busy}
                      onChange={(event) =>
                        act(
                          () =>
                            workspaces.setRole(
                              workspace.id,
                              member.user_id,
                              event.target.value as "viewer" | "editor" | "owner",
                            ),
                          "Role changed",
                        )
                      }
                    >
                      {ROLES.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.value}
                        </option>
                      ))}
                    </select>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove ${member.email}`}
                      disabled={busy}
                      onClick={() =>
                        act(
                          () => workspaces.removeMember(workspace.id, member.user_id),
                          "Member removed",
                        )
                      }
                    >
                      Remove
                    </Button>
                  </>
                ) : (
                  <Badge variant="outline">{member.role}</Badge>
                )}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="mb-1 font-medium">Shared here</h3>
          {(workspace.datasets ?? []).length + (workspace.dashboards ?? []).length === 0 ? (
            <p className="text-muted-foreground">
              Nothing yet. Owners of a dataset or dashboard can share it from its page.
            </p>
          ) : (
            <ul className="list-disc pl-5">
              {(workspace.datasets ?? []).map((item) => (
                <li key={item.id}>
                  Dataset:{" "}
                  <Link className="underline" href={`/datasets/${item.id}`}>
                    {item.name}
                  </Link>
                </li>
              ))}
              {(workspace.dashboards ?? []).map((item) => (
                <li key={item.id}>
                  Dashboard:{" "}
                  <Link className="underline" href={`/dashboards/${item.id}`}>
                    {item.name}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
        {owner ? (
          <div className="space-y-2 rounded-md border p-3" data-testid="invite-form">
            <h3 className="font-medium">Invite someone</h3>
            <div className="flex flex-wrap gap-2">
              <Input
                aria-label="Invite email"
                placeholder="name@company.com"
                className="max-w-xs"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              <select
                aria-label="Invite role"
                className={`${selectClass} h-9`}
                value={role}
                onChange={(event) => setRole(event.target.value as "viewer" | "editor" | "owner")}
              >
                {ROLES.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
              <Button
                type="button"
                size="sm"
                disabled={busy || !email.includes("@")}
                onClick={() =>
                  act(async () => {
                    const created = await workspaces.invite(workspace.id, email.trim(), role);
                    setLink(`${window.location.origin}/invite#${created.secret}`);
                    setEmail("");
                  })
                }
              >
                Create invitation
              </Button>
            </div>
            {link ? (
              <div className="space-y-1">
                <p className="text-xs">
                  Send this link to the person yourself (InsightForge does not send email). It works
                  once, for that email address only, for 7 days.
                </p>
                <code
                  className="block break-all rounded bg-muted px-2 py-1"
                  data-testid="invite-url"
                >
                  {link}
                </code>
              </div>
            ) : null}
            {(workspace.invites ?? []).filter((item) => !item.accepted_at && !item.revoked_at)
              .length ? (
              <ul className="text-xs text-muted-foreground">
                {(workspace.invites ?? [])
                  .filter((item) => !item.accepted_at && !item.revoked_at)
                  .map((item) => (
                    <li key={item.id} className="flex items-center gap-2">
                      Waiting: {item.email} ({item.role})
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() =>
                          act(
                            () => workspaces.revokeInvite(workspace.id, item.id),
                            "Invitation revoked",
                          )
                        }
                      >
                        Revoke
                      </Button>
                    </li>
                  ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default function WorkspacesPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["workspaces"], queryFn: workspaces.list });
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function create() {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      await workspaces.create(name.trim());
      setName("");
      await client.invalidateQueries({ queryKey: ["workspaces"] });
      toast.success("Workspace created");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not create the workspace");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold">Workspaces</h1>
        <p className="text-muted-foreground">
          Work with your team. Share a dataset or dashboard with a workspace from its page. Viewers
          can read and ask questions, editors can also change definitions and data, owners also
          manage members. Only a dataset&apos;s owner can change its privacy, stop sharing it or
          delete it.
        </p>
      </div>
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void create();
        }}
      >
        <Input
          aria-label="New workspace name"
          placeholder="Finance team"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <Button type="submit" disabled={busy || !name.trim()}>
          New workspace
        </Button>
      </form>
      {query.data && query.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">You are not in a workspace yet.</p>
      ) : null}
      <div className="space-y-4">
        {(query.data ?? []).map((workspace) => (
          <WorkspaceCard key={workspace.id} workspace={workspace} />
        ))}
      </div>
    </div>
  );
}
