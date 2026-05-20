// SnapshotView — extracts the #415 cross-lane snapshot surface from App.tsx
// so App.tsx can host the top-level Tabs nav (#416).

import { useState } from "react";
import { FilterBar } from "./FilterBar";
import { LaneTable } from "./LaneTable";
import { useSnapshot } from "@/hooks/useSnapshot";
import { redactHomePaths } from "@/lib/redact";

export function SnapshotView() {
  const { jobsByLane, lanes, error, loading, elapsedNonce, refresh } =
    useSnapshot();
  const [filter, setFilter] = useState("");

  return (
    <div className="flex flex-col gap-4">
      {/* Filter + refresh toolbar */}
      <FilterBar
        value={filter}
        onChange={setFilter}
        onRefresh={refresh}
        loading={loading}
      />

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 dark:bg-red-950 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-300">
          <strong>Error loading data:</strong>{" "}
          {String(redactHomePaths(error))}
          <button
            className="ml-4 underline text-red-600 dark:text-red-400 hover:no-underline"
            onClick={() => refresh()}
          >
            Retry
          </button>
        </div>
      )}

      {loading && !error && (
        <div className="flex items-center justify-center py-16 text-slate-400 text-sm">
          Loading snapshot…
        </div>
      )}

      {!loading && (
        <LaneTable
          jobsByLane={jobsByLane}
          lanes={lanes}
          filterRaw={filter}
          elapsedNonce={elapsedNonce}
        />
      )}

      <p className="text-xs text-slate-400">
        Auto-refresh on file-watcher event &middot;{" "}
        {Object.values(jobsByLane).flat().length} session(s) across{" "}
        {lanes.length} lane(s)
      </p>
    </div>
  );
}
