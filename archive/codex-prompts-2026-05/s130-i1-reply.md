ACCEPTED — fixed in `d00bf15` (just pushed).

The visibility-skip throttle introduced in the previous fix did leave a stale-data window of up to `intervalMs` after the user returns. Addressed by subscribing to `document.visibilitychange` inside the same `useEffect` and firing an immediate refresh when visibility transitions to `visible`:

```ts
const tryTick = () => {
  if (loadingRef.current) return;
  if (authErrorRef.current) return;
  if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
  refresh(false);
};
const onVisibilityChange = () => {
  if (typeof document !== "undefined" && document.visibilityState === "visible") {
    tryTick();
  }
};
const id = setInterval(tryTick, autoRefreshIntervalMs);
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", onVisibilityChange);
}
return () => {
  clearInterval(id);
  if (typeof document !== "undefined") {
    document.removeEventListener("visibilitychange", onVisibilityChange);
  }
};
```

Same guards apply on the resume path (loadingRef + authErrorRef), so no stacking of overlapping refreshes and no hammering when auth is broken. Cleanup tears down both the interval and the listener on unmount, toggle ON→OFF, or interval change.

Now the contract is: user returns to a visible window → fresh data within one IPC round-trip (~50ms) instead of up to `intervalMs`.
