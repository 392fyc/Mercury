// LaneTable — full cross-lane snapshot table.
// Renders known lanes first, then the __unassigned__ bucket if non-empty.
// Applies filter tokens to hide non-matching rows AND to align the row's
// displayed job with the filter (#432 — most-recent MATCHING job, not most-
// recent overall job, so the surfaced row never contradicts the active filter).

import { useState } from "react";
import { LaneRow } from "./LaneRow";
import { JobDetailDialog } from "./JobDetailDialog";
import { laneMatchesFilter, matchesJob, parseFilter } from "@/lib/filter";
import type { JobState, Lane } from "@/lib/types";

// Canonical lane order for v1
const CANONICAL_LANES = ["main", "side-bug", "side-sot", "health"];

interface LaneTableProps {
  jobsByLane: Record<string, JobState[]>;
  lanes: Lane[];
  filterRaw: string;
  elapsedNonce: number;
}

function buildLaneList(serverLanes: Lane[]): Lane[] {
  // Start with canonical lanes (always show even if empty)
  const seen = new Set<string>();
  const result: Lane[] = [];

  for (const name of CANONICAL_LANES) {
    const serverLane = serverLanes.find((l) => l.name === name);
    result.push(serverLane ?? { name });
    seen.add(name);
  }

  // Add any extra lanes from server that aren't canonical
  for (const lane of serverLanes) {
    if (!seen.has(lane.name) && lane.name !== "__unassigned__") {
      result.push(lane);
      seen.add(lane.name);
    }
  }

  return result;
}

export function LaneTable({
  jobsByLane,
  lanes,
  filterRaw,
  elapsedNonce,
}: LaneTableProps) {
  const [selectedJob, setSelectedJob] = useState<JobState | null>(null);
  const [selectedLane, setSelectedLane] = useState<string>("");
  const [dialogOpen, setDialogOpen] = useState(false);

  const tokens = parseFilter(filterRaw);
  // Job-relevant tokens drive the row's displayed job; `l:` tokens select
  // lanes only and are filtered out so they don't accidentally exclude jobs.
  const jobTokens = tokens.filter((t) => t.kind !== "lane");
  const laneList = buildLaneList(lanes);
  const unassigned = jobsByLane["__unassigned__"] ?? [];

  function handleSelect(laneName: string, job: JobState) {
    setSelectedLane(laneName);
    setSelectedJob(job);
    setDialogOpen(true);
  }

  // Display-job picker: matching jobs only, so the row never contradicts the
  // active filter. Empty `jobTokens` → all jobs match → same list as before.
  function matchingJobs(laneName: string, jobs: JobState[]): JobState[] {
    if (jobTokens.length === 0) return jobs;
    return jobs.filter((job) => matchesJob(job, laneName, jobTokens));
  }

  const visibleLanes = laneList.filter((lane) => {
    const jobs = jobsByLane[lane.name] ?? [];
    return laneMatchesFilter(lane.name, jobs, tokens);
  });

  const showUnassigned =
    unassigned.length > 0 &&
    laneMatchesFilter("__unassigned__", unassigned, tokens);

  return (
    <>
      <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
        <table className="w-full text-left text-sm" role="table">
          <thead className="bg-slate-100 dark:bg-slate-800 text-xs uppercase tracking-wide text-slate-600 dark:text-slate-400">
            <tr>
              <th className="px-3 py-2 font-semibold">Lane</th>
              <th className="px-3 py-2 font-semibold">Session</th>
              <th className="px-3 py-2 font-semibold">State</th>
              <th className="px-3 py-2 font-semibold">Template</th>
              <th className="px-3 py-2 font-semibold">Intent</th>
              <th className="px-3 py-2 font-semibold">Elapsed</th>
              <th className="px-3 py-2 font-semibold text-slate-400">PR #</th>
            </tr>
          </thead>
          <tbody className="bg-white dark:bg-slate-900 divide-y divide-slate-100 dark:divide-slate-800">
            {visibleLanes.map((lane) => (
              <LaneRow
                key={lane.name}
                lane={lane}
                jobs={matchingJobs(lane.name, jobsByLane[lane.name] ?? [])}
                onSelect={(job) => handleSelect(lane.name, job)}
                elapsedNonce={elapsedNonce}
              />
            ))}
            {showUnassigned && (
              <LaneRow
                key="__unassigned__"
                lane={{ name: "__unassigned__" }}
                jobs={matchingJobs("__unassigned__", unassigned)}
                onSelect={(job) => handleSelect("__unassigned__", job)}
                elapsedNonce={elapsedNonce}
              />
            )}
            {visibleLanes.length === 0 && !showUnassigned && (
              <tr>
                <td
                  colSpan={7}
                  className="px-3 py-8 text-center text-slate-400 text-sm"
                >
                  No sessions match the current filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <JobDetailDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        laneName={selectedLane}
        job={selectedJob}
      />
    </>
  );
}
