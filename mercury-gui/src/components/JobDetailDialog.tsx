// JobDetailDialog — full state.json viewer for a single JobState.
// Read-only: no form, no mutations, just JSON dump in a scrollable pre block.

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { redactHomePaths } from "@/lib/redact";
import type { JobState } from "@/lib/types";

interface JobDetailDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  laneName: string;
  job: JobState | null;
}

export function JobDetailDialog({
  open,
  onOpenChange,
  laneName,
  job,
}: JobDetailDialogProps) {
  if (!job) return null;

  const title = `${laneName} · ${job.daemonShort || job.sessionId?.slice(0, 8) || "—"}`;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="font-mono text-base">{title}</DialogTitle>
          <DialogDescription>
            Full state snapshot — read-only
          </DialogDescription>
        </DialogHeader>
        {/* PII two-surface contract:
            - Rust-side (#414): JobState::sanitize_paths scrubs cwd/linkScanPath/
              worktreePath only.
            - Frontend recursive redact (defense-in-depth, S125): walks the full
              object tree to scrub home paths embedded in output/children/etc
              that the narrow Rust contract does not cover. */}
        <pre className="overflow-auto flex-1 text-xs font-mono bg-slate-950 text-slate-100 rounded-md p-4 leading-relaxed">
          {JSON.stringify(redactHomePaths(job), null, 2)}
        </pre>
      </DialogContent>
    </Dialog>
  );
}
