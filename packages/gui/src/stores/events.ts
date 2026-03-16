/**
 * Event log store — tracks all Mercury events from the orchestrator.
 */

import { ref, computed } from "vue";
import type { MercuryEvent } from "../lib/tauri-bridge";
import { onMercuryEvent } from "../lib/tauri-bridge";

const events = ref<MercuryEvent[]>([]);

const eventCount = computed(() => events.value.length);

function addEvent(event: MercuryEvent) {
  events.value = [...events.value, event];
}

let eventListenersInitialized = false;

async function initEventListeners() {
  if (eventListenersInitialized) return;
  eventListenersInitialized = true;

  await onMercuryEvent((event) => {
    addEvent(event);
  });
}

export function useEventStore() {
  return {
    events,
    eventCount,
    addEvent,
    initEventListeners,
  };
}
