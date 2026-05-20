// StateBadge — renders a state token as a colored badge with display label.

import { Badge } from "@/components/ui/badge";
import { stateToLabel, labelToTone } from "@/lib/state-mapping";
import type { Tone } from "@/lib/state-mapping";

interface StateBadgeProps {
  state: string | null | undefined;
}

// Map Tone → Badge variant
function toneToVariant(tone: Tone) {
  switch (tone) {
    case "success":
      return "success" as const;
    case "info":
      return "info" as const;
    case "warning":
      return "warning" as const;
    case "danger":
      return "danger" as const;
    case "neutral":
      return "neutral" as const;
  }
}

export function StateBadge({ state }: StateBadgeProps) {
  const label = stateToLabel(state);
  const tone = labelToTone(label);
  return <Badge variant={toneToVariant(tone)}>{label}</Badge>;
}
