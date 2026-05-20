// IssueRow — single row in the Issues section of the GitHub dashboard (#416).
// Clickable → opens issue URL in browser via tauri-plugin-opener.

import { Badge } from "@/components/ui/badge";
import { elapsed } from "@/lib/elapsed";
import type { GhIssue } from "@/lib/ghTypes";
import { safeOpenUrl } from "@/lib/safeOpenUrl";

interface IssueRowProps {
  issue: GhIssue;
}

export function IssueRow({ issue }: IssueRowProps) {
  function handleOpen() {
    safeOpenUrl(issue.url).catch(() => {
      // Non-fatal: URL open failure (or whitelist rejection) doesn't break the dashboard
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
      aria-label={`Open issue #${issue.number}: ${issue.title}`}
    >
      <td className="px-3 py-2 font-mono text-xs whitespace-nowrap text-slate-500 dark:text-slate-400">
        #{issue.number}
      </td>
      <td className="px-3 py-2 text-sm max-w-xs">
        <span className="text-blue-600 dark:text-blue-400 hover:underline">
          {issue.title}
        </span>
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        <Badge variant="success" className="text-xs">
          {issue.state}
        </Badge>
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {issue.labels.map((label) => (
            <Badge key={label.name} variant="neutral" className="text-xs">
              {label.name}
            </Badge>
          ))}
        </div>
      </td>
      <td className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap tabular-nums">
        {elapsed(issue.updatedAt)}
      </td>
    </tr>
  );
}
