/**
 * Mercury Event Bus
 *
 * Append-only, immutable event log.
 * Pattern from: OpenHands event-sourcing + Claude Code hooks
 *
 * All agent interactions are typed events that can be:
 * - Replayed for debugging
 * - Used for session recovery
 * - Streamed to GUI for real-time display
 */

import { randomUUID } from "node:crypto";
import type { EventType, MercuryEvent } from "./types.js";

type EventHandler<T = unknown> = (event: MercuryEvent<T>) => void;
type EventFilter = (event: MercuryEvent) => boolean;

export class EventBus {
  /** Append-only event log */
  private log: MercuryEvent[] = [];

  /** Subscribers by event type */
  private handlers = new Map<EventType | "*", Set<EventHandler>>();

  /** Filtered subscribers */
  private filteredHandlers: Array<{
    filter: EventFilter;
    handler: EventHandler;
  }> = [];

  /**
   * Emit a new event to the bus.
   * Events are immutable once emitted.
   */
  emit<T>(
    type: EventType,
    agentId: string,
    sessionId: string,
    payload: T,
    parentEventId?: string,
  ): MercuryEvent<T> {
    const event: MercuryEvent<T> = Object.freeze({
      id: randomUUID(),
      type,
      timestamp: Date.now(),
      agentId,
      sessionId,
      payload,
      parentEventId,
    });

    // Append to immutable log
    this.log.push(event as MercuryEvent);

    // Notify type-specific handlers
    const typeHandlers = this.handlers.get(type);
    if (typeHandlers) {
      for (const handler of typeHandlers) {
        handler(event as MercuryEvent);
      }
    }

    // Notify wildcard handlers
    const wildcardHandlers = this.handlers.get("*");
    if (wildcardHandlers) {
      for (const handler of wildcardHandlers) {
        handler(event as MercuryEvent);
      }
    }

    // Notify filtered handlers
    for (const { filter, handler } of this.filteredHandlers) {
      if (filter(event as MercuryEvent)) {
        handler(event as MercuryEvent);
      }
    }

    return event;
  }

  /**
   * Subscribe to events by type.
   * Use "*" for all events.
   */
  on(type: EventType | "*", handler: EventHandler): () => void {
    let set = this.handlers.get(type);
    if (!set) {
      set = new Set();
      this.handlers.set(type, set);
    }
    set.add(handler);

    // Return unsubscribe function
    return () => set!.delete(handler);
  }

  /**
   * Subscribe with a custom filter (e.g. by agentId, sessionId).
   */
  onFiltered(filter: EventFilter, handler: EventHandler): () => void {
    const entry = { filter, handler };
    this.filteredHandlers.push(entry);
    return () => {
      const idx = this.filteredHandlers.indexOf(entry);
      if (idx >= 0) this.filteredHandlers.splice(idx, 1);
    };
  }

  /**
   * Get events for a specific session (for replay/debugging).
   */
  getSessionEvents(sessionId: string): readonly MercuryEvent[] {
    return this.log.filter((e) => e.sessionId === sessionId);
  }

  /**
   * Get events for a specific agent.
   */
  getAgentEvents(agentId: string): readonly MercuryEvent[] {
    return this.log.filter((e) => e.agentId === agentId);
  }

  /**
   * Get full event log (readonly).
   */
  getLog(): readonly MercuryEvent[] {
    return this.log;
  }

  /**
   * Get event count (for monitoring).
   */
  get size(): number {
    return this.log.length;
  }
}
