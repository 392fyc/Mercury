DISAGREE on this finding — `refresh(false)` cannot return a rejected promise, so a `.catch()` here is dead defensive code.

`useGitHubData.refresh` is implemented as a `useCallback(async (force) => { ... })` whose entire body sits inside `try / catch / finally` (see `mercury-gui/src/hooks/useGitHubData.ts:37-66`):

```ts
const refresh = useCallback(async (force: boolean = false) => {
  const myId = ++reqIdRef.current;
  setLoading(true);
  try {
    const auth = await invoke<GhAuthStatus>("check_gh_auth");
    ...
    const result = await invoke<GhSnapshot>("fetch_gh_dashboard", { force });
    ...
  } catch (e) {
    if (myId !== reqIdRef.current) return;
    setAuthError(undefined);
    setError(String(e));
  } finally {
    if (myId === reqIdRef.current) setLoading(false);
  }
}, []);
```

Both `await invoke(...)` calls are inside the `try` block; any thrown / rejected promise from the Tauri IPC layer is caught and routed to `setError(String(e))`. The catch + finally bodies only call React state setters (`setAuthError` / `setError` / `setLoading`), which do not throw — React 19 state setters are documented as never throwing, and they no-op on unmounted components without warning.

Therefore `refresh(false)` resolves with `undefined` in every reachable code path (success OR caught failure). There is no scenario in which the interval handler observes a rejected promise, so the proposed `void Promise.resolve(refresh(false)).catch(() => {})` wrapper would never execute its catch arm.

CLAUDE.md explicitly forbids this pattern: *"Don't add error handling, fallbacks, or validation for scenarios that can't happen. Trust internal code and framework guarantees."*

If `useGitHubData.refresh` is ever refactored to bubble rejections (currently it does not, by design), the right fix is to restore the try/catch at that site — not to bolt a defensive `.catch()` onto every consumer. The hook's failure-handling contract is owned by `useGitHubData`.

The non-blocking `refresh(false)` call site is intentional: the interval fires-and-forgets because `refresh()` updates React state internally and the dashboard re-renders from that state. There is nothing to `await` and no Promise to handle at this layer.

F1 + F2 ACCEPT-FIX (interval whitelist) is already applied in the upcoming push.
