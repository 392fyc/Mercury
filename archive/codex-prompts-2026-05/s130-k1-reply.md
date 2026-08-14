ACCEPTED — rewritten in English-only in commit 52be066. The Chinese phrase "行为偏差" (which echoed the original Argus finding label) is replaced with descriptive English that retains the rationale:

- Why we skip ticks when the window is hidden
- Why we subscribe to `visibilitychange` for an immediate refresh on resume (avoiding up to `intervalMs` of stale data when the user returns to the dashboard)
- What guards apply on each path (`loadingRef` + `authErrorRef` + `visibilityState`)

No behavior change — comment-only diff. Build clean (294.31 kB unchanged).
