import { Badge } from "@/components/ui/badge";
import type { RunEvent } from "@/lib/sse";
import { cn } from "@/lib/utils";

export function StepTimeline({ events }: { events: RunEvent[] }) {
  if (!events.length) return null;
  return (
    <div className="space-y-2 border-l pl-4">
      {events
        .filter((event) => !["done"].includes(event.type))
        .map((event, index) => (
          <div
            className="flex items-center gap-2 text-xs"
            key={`${event.type}-${event.name}-${index}`}
          >
            <span
              className={cn(
                "size-2 rounded-full",
                event.type === "error"
                  ? "bg-destructive"
                  : event.type === "step_done"
                    ? "bg-emerald-500"
                    : "bg-blue-500",
              )}
            />
            <span className="font-medium">{event.name ?? event.type.replace("_", " ")}</span>
            {event.action && <Badge variant="outline">{event.action}</Badge>}
          </div>
        ))}
    </div>
  );
}
