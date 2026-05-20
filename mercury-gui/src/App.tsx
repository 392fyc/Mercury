// Mercury GUI — top-level shell with Tabs nav (#416).
// Tab 1: Cross-lane snapshot view (Phase 6 slice C, #415).
// Tab 2: Issue/PR dashboard (Phase 6 slice D, #416).

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SnapshotView } from "./components/SnapshotView";
import { GitHubDashboard } from "./components/GitHubDashboard";

function App() {
  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100 flex flex-col">
      {/* Header — outside tabs so branding stays visible on both views */}
      <header className="border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 px-4 py-3 shadow-sm">
        <h1 className="text-lg font-bold tracking-tight">
          Mercury
          <span className="ml-2 text-xs font-normal text-slate-400 uppercase tracking-widest">
            monitor
          </span>
        </h1>
      </header>

      {/* Tabs */}
      <main className="flex-1 px-4 py-4 overflow-auto">
        <Tabs defaultValue="snapshot" className="flex flex-col gap-4">
          <TabsList className="w-fit">
            <TabsTrigger value="snapshot">Snapshot</TabsTrigger>
            <TabsTrigger value="issues">Issues / PRs</TabsTrigger>
          </TabsList>

          <TabsContent value="snapshot">
            <SnapshotView />
          </TabsContent>

          <TabsContent value="issues">
            <GitHubDashboard />
          </TabsContent>
        </Tabs>
      </main>
    </div>
  );
}

export default App;
