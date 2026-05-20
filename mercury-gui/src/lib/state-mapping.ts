// State token → display label mapping.
// Forward-compatible: unknown tokens produce "Unknown" rather than throwing.

export type DisplayLabel =
  | "Working"
  | "Idle"
  | "Needs input"
  | "Completed"
  | "Failed"
  | "Stopped"
  | "Unknown";

export function stateToLabel(state: string | null | undefined): DisplayLabel {
  if (!state) return "Unknown"; // null/empty defensive
  switch (state.toLowerCase()) {
    case "done":
      return "Completed";
    case "working":
      return "Working";
    case "idle":
      return "Idle";
    case "needs_input":
      return "Needs input";
    case "failed":
      return "Failed";
    case "stopped":
      return "Stopped";
    default:
      return "Unknown"; // forward-compat: unknown token → safe display
  }
}

export type Tone = "success" | "info" | "warning" | "danger" | "neutral";

export function labelToTone(label: DisplayLabel): Tone {
  switch (label) {
    case "Completed":
      return "success";
    case "Working":
      return "info";
    case "Needs input":
      return "warning";
    case "Failed":
      return "danger";
    case "Stopped":
      return "neutral";
    case "Idle":
      return "neutral";
    case "Unknown":
      return "neutral";
  }
}
