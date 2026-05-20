// Mercury GUI — Cross-lane snapshot view (Phase 6 slice C, Issue #415)
// Replaces the Tauri scaffold template. Consumes the #414 IPC data layer.

import { useState } from "react";
import { FilterBar } from "./components/FilterBar";
import { LaneTable } from "./components/LaneTable";
import { useSnapshot } from "./hooks/useSnapshot";
import { redactHomePaths } from "./lib/redact";

function App() {
  const { jobsByLane, lanes, error, loading, elapsedNonce, refresh } =
    useSnapshot();
  const [filter, setFilter] = useState("");

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100 flex flex-col">
      {/* Header */}
      <header className="border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 px-4 py-3 flex items-center gap-4 shadow-sm">
        <h1 className="text-lg font-bold tracking-tight shrink-0">
          Mercury
          <span className="ml-2 text-xs font-normal text-slate-400 uppercase tracking-widest">
            monitor
          </span>
        </h1>
        <div className="flex-1">
          <FilterBar
            value={filter}
            onChange={setFilter}
            onRefresh={refresh}
            loading={loading}
          />
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 px-4 py-4 overflow-auto">
        {error && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 dark:bg-red-950 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-300">
            {/* IPC error strings may carry host paths or supervisor details;
                run them through redactHomePaths defense-in-depth (Argus iter-1
                Critical/security finding). */}
            <strong>Error loading data:</strong> {String(redactHomePaths(error))}
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
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-200 dark:border-slate-800 px-4 py-2 text-xs text-slate-400 flex gap-4">
        <span>Auto-refresh on file-watcher event</span>
        <span>·</span>
        <span>
          {Object.values(jobsByLane).flat().length} session(s) across{" "}
          {lanes.length} lane(s)
        </span>
      </footer>
    </div>
  );
}

export default App;
