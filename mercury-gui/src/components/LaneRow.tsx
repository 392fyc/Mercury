// LaneRow — one row in the cross-lane snapshot table.
// Shows the most-recent job per lane plus "+N more" indicator.

import { StateBadge } from "./StateBadge";
import { elapsed } from "@/lib/elapsed";
import type { JobState, Lane } from "@/lib/types";

interface LaneRowProps {
  lane: Lane;
  jobs: JobState[];
  onSelect: (job: JobState) => void;
  // elapsedNonce forces re-render for elapsed freshness without data fetch
  elapsedNonce: number;
}

function mostRecent(jobs: JobState[]): JobState | null {
  if (jobs.length === 0) return null;
  return jobs.reduce((best, cur) => {
    if (!best.createdAt) return cur;
    if (!cur.createdAt) return best;
    return cur.createdAt > best.createdAt ? cur : best;
  });
}

function truncate(str: string | undefined, max: number): string {
  if (!str) return "—";
  return str.length > max ? str.slice(0, max) + "…" : str;
}

export function LaneRow({ lane, jobs, onSelect, elapsedNonce: _elapsedNonce }: LaneRowProps) {
  const job = mostRecent(jobs);
  const extraCount = jobs.length > 1 ? jobs.length - 1 : 0;

  return (
    <tr
      className="border-b border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors cursor-pointer"
      onClick={() => { if (job) onSelect(job); }}
      role="row"
      tabIndex={0}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && job) onSelect(job);
      }}
      aria-label={`Lane ${lane.name}`}
    >
      {/* Lane name */}
      <td className="px-3 py-2 font-bold text-sm whitespace-nowrap">
        {lane.name}
      </td>

      {/* Active session daemonShort */}
      <td className="px-3 py-2 font-mono text-xs whitespace-nowrap">
        {job ? (
          <span className="text-blue-600 dark:text-blue-400 hover:underline">
            {job.daemonShort || job.sessionId?.slice(0, 8) || "—"}
          </span>
        ) : (
          <span className="text-slate-400">—</span>
        )}
        {extraCount > 0 && (
          <span className="ml-1 text-slate-400 text-xs">+{extraCount} more</span>
        )}
      </td>

      {/* State badge */}
      <td className="px-3 py-2 whitespace-nowrap">
        {job ? <StateBadge state={job.state} /> : <span className="text-slate-400 text-sm">—</span>}
      </td>

      {/* Template */}
      <td className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
        {job?.template || "—"}
      </td>

      {/* Intent */}
      <td className="px-3 py-2 text-xs max-w-xs">
        <span title={job?.intent || ""}>
          {truncate(job?.intent, 60)}
        </span>
      </td>

      {/* Elapsed */}
      <td className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap tabular-nums">
        {job ? elapsed(job.createdAt) : "—"}
      </td>

      {/* PR # — deferred to #416 */}
      <td className="px-3 py-2 text-xs text-slate-300 dark:text-slate-600 whitespace-nowrap">
        —
      </td>
    </tr>
  );
}
