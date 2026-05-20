// FilterBar — filter input + manual refresh button.
// Supports a:<template>, s:<state>, l:<lane>, and free-text tokens (AND-combined).

import { RefreshCw } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

interface FilterBarProps {
  value: string;
  onChange: (value: string) => void;
  onRefresh: () => void;
  loading: boolean;
}

export function FilterBar({
  value,
  onChange,
  onRefresh,
  loading,
}: FilterBarProps) {
  return (
    <div className="flex gap-2 items-center">
      <Input
        className="flex-1 font-mono text-sm"
        placeholder='Filter: a:dev  s:working  l:main  or free text'
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Filter sessions"
      />
      <Button
        variant="outline"
        size="sm"
        onClick={() => onRefresh()}
        disabled={loading}
        aria-label="Refresh"
        title="Refresh data"
      >
        <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        <span className="ml-1 hidden sm:inline">Refresh</span>
      </Button>
    </div>
  );
}
