// Cherry-picked from obra/superpowers (MIT, Copyright 2025 Jesse Vincent)
// Source: https://github.com/obra/superpowers/blob/917e5f5/skills/systematic-debugging/condition-based-waiting-example.ts
// SHA: 917e5f53b16b115b70a3a355ed5f4993b9f8b73d
// Date: 2026-04-10
// Issue: #209

// REFERENCE EXAMPLE — not meant to be compiled directly.
// Shows condition-based waiting pattern from Lace test infrastructure (2025-10-03).
// Adapt the types and imports to your own project when applying this pattern.

// Original imports (Lace-specific, shown for context):
// import type { ThreadManager } from '~/threads/thread-manager';
// import type { LaceEvent, LaceEventType } from '~/threads/types';

// Generic type placeholders for the pattern:
type ThreadManager = { getEvents(threadId: string): LaceEvent[] };
type LaceEvent = { type: string; data?: unknown };
type LaceEventType = string;

/**
 * Wait for a specific event type to appear in thread
 *
 * @param threadManager - The thread manager to query
 * @param threadId - Thread to check for events
 * @param eventType - Type of event to wait for
 * @param timeoutMs - Maximum time to wait (default 5000ms)
 * @returns Promise resolving to the first matching event
 *
 * Example:
 *   await waitForEvent(threadManager, agentThreadId, 'TOOL_RESULT');
 */
export function waitForEvent(
  threadManager: ThreadManager,
  threadId: string,
  eventType: LaceEventType,
  timeoutMs = 5000
): Promise<LaceEvent> {
  return new Promise((resolve, reject) => {
    const startTime = Date.now();

    const check = () => {
      try {
        const events = threadManager.getEvents(threadId);
        const event = events.find((e) => e.type === eventType);

        if (event) {
          resolve(event);
          return;
        }

        if (Date.now() - startTime > timeoutMs) {
          reject(new Error(`Timeout waiting for ${eventType} event after ${timeoutMs}ms`));
          return;
        }

        setTimeout(check, 10); // Poll every 10ms for efficiency
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    };

    check();
  });
}

/**
 * Wait for a specific number of events of a given type
 *
 * @param threadManager - The thread manager to query
 * @param threadId - Thread to check for events
 * @param eventType - Type of event to wait for
 * @param count - Number of events to wait for
 * @param timeoutMs - Maximum time to wait (default 5000ms)
 * @returns Promise resolving to all matching events once count is reached
 *
 * Example:
 *   // Wait for 2 AGENT_MESSAGE events (initial response + continuation)
 *   await waitForEventCount(threadManager, agentThreadId, 'AGENT_MESSAGE', 2);
 */
export function waitForEventCount(
  threadManager: ThreadManager,
  threadId: string,
  eventType: LaceEventType,
  count: number,
  timeoutMs = 5000
): Promise<LaceEvent[]> {
  return new Promise((resolve, reject) => {
    const startTime = Date.now();

    const check = () => {
      try {
        const events = threadManager.getEvents(threadId);
        const matchingEvents = events.filter((e) => e.type === eventType);

        if (matchingEvents.length >= count) {
          resolve(matchingEvents);
          return;
        }

        if (Date.now() - startTime > timeoutMs) {
          reject(
            new Error(
              `Timeout waiting for ${count} ${eventType} events after ${timeoutMs}ms (got ${matchingEvents.length})`
            )
          );
          return;
        }

        setTimeout(check, 10);
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    };

    check();
  });
}

/**
 * Wait for an event matching a custom predicate
 * Useful when you need to check event data, not just type
 *
 * @param threadManager - The thread manager to query
 * @param threadId - Thread to check for events
 * @param predicate - Function that returns true when event matches
 * @param description - Human-readable description for error messages
 * @param timeoutMs - Maximum time to wait (default 5000ms)
 * @returns Promise resolving to the first matching event
 *
 * Example:
 *   // Wait for TOOL_RESULT with specific ID
 *   await waitForEventMatch(
 *     threadManager,
 *     agentThreadId,
 *     (e) => e.type === 'TOOL_RESULT' && e.data.id === 'call_123',
 *     'TOOL_RESULT with id=call_123'
 *   );
 */
export function waitForEventMatch(
  threadManager: ThreadManager,
  threadId: string,
  predicate: (event: LaceEvent) => boolean,
  description: string,
  timeoutMs = 5000
): Promise<LaceEvent> {
  return new Promise((resolve, reject) => {
    const startTime = Date.now();

    const check = () => {
      try {
        const events = threadManager.getEvents(threadId);
        const event = events.find(predicate);

        if (event) {
          resolve(event);
          return;
        }

        if (Date.now() - startTime > timeoutMs) {
          reject(new Error(`Timeout waiting for ${description} after ${timeoutMs}ms`));
          return;
        }

        setTimeout(check, 10);
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    };

    check();
  });
}

// Usage example from actual debugging session:
//
// BEFORE (flaky):
// ---------------
// const messagePromise = agent.sendMessage('Execute tools');
// await new Promise(r => setTimeout(r, 300)); // Hope tools start in 300ms
// agent.abort();
// await messagePromise;
// await new Promise(r => setTimeout(r, 50));  // Hope results arrive in 50ms
// expect(toolResults.length).toBe(2);         // Fails randomly
//
// AFTER (reliable):
// ----------------
// const messagePromise = agent.sendMessage('Execute tools');
// await waitForEventCount(threadManager, threadId, 'TOOL_CALL', 2); // Wait for tools to start
// agent.abort();
// await messagePromise;
// await waitForEventCount(threadManager, threadId, 'TOOL_RESULT', 2); // Wait for results
// expect(toolResults.length).toBe(2); // Always succeeds
//
// Result: 60% pass rate -> 100%, 40% faster execution
