// PullRequestRow — single row in the PRs section of the GitHub dashboard (#416).
// Clickable → opens PR URL in browser via tauri-plugin-opener.

import { Badge } from "@/components/ui/badge";
import { elapsed } from "@/lib/elapsed";
import type { GhPullRequest } from "@/lib/ghTypes";
import { safeOpenUrl } from "@/lib/safeOpenUrl";

interface PullRequestRowProps {
  pr: GhPullRequest;
}

function reviewDecisionVariant(
  decision: string | null | undefined
): "success" | "warning" | "danger" | "neutral" {
  if (!decision) return "neutral";
  const d = decision.toLowerCase();
  if (d === "approved") return "success";
  if (d === "changes_requested") return "danger";
  if (d === "review_required") return "warning";
  return "neutral";
}

export function PullRequestRow({ pr }: PullRequestRowProps) {
  function handleOpen() {
    safeOpenUrl(pr.url).catch(() => {
      // Non-fatal (URL open failure or whitelist rejection)
    });
  }

  return (
    <tr
      className="border-b border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors cursor-pointer"
      onClick={handleOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleOpen();
        }
      }}
      aria-label={`Open pull request #${pr.number}: ${pr.title}`}
    >
      <td className="px-3 py-2 font-mono text-xs whitespace-nowrap text-slate-500 dark:text-slate-400">
        #{pr.number}
      </td>
      <td className="px-3 py-2 text-sm max-w-xs">
        <span className="text-blue-600 dark:text-blue-400 hover:underline">
          {pr.title}
        </span>
        <span className="ml-2 text-xs text-slate-400 font-mono whitespace-nowrap">
          {pr.headRefName}→{pr.baseRefName}
        </span>
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        <div className="flex gap-1 flex-wrap">
          {pr.isDraft && (
            <Badge variant="neutral" className="text-xs">
              draft
            </Badge>
          )}
          <Badge variant="info" className="text-xs">
            {pr.state}
          </Badge>
          {pr.reviewDecision && (
            <Badge
              variant={reviewDecisionVariant(pr.reviewDecision)}
              className="text-xs"
            >
              {pr.reviewDecision.toLowerCase().replace(/_/g, " ")}
            </Badge>
          )}
        </div>
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {pr.labels.map((label) => (
            <Badge key={label.name} variant="neutral" className="text-xs">
              {label.name}
            </Badge>
          ))}
        </div>
      </td>
      <td className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap tabular-nums">
        {elapsed(pr.updatedAt)}
      </td>
    </tr>
  );
}
