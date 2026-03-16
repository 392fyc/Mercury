<script setup lang="ts">
import { computed, ref, watch, nextTick } from "vue";
import type { SlashCommand } from "../lib/tauri-bridge";

const props = defineProps<{
  commands: SlashCommand[];
  query: string; // current text after "/" e.g. "co" for "/co"
  visible: boolean;
}>();

const emit = defineEmits<{
  (e: "select", command: SlashCommand): void;
  (e: "close"): void;
}>();

const activeIndex = ref(0);
const listEl = ref<HTMLDivElement>();

// Filter commands by query (fuzzy match on name)
const filteredCommands = computed(() => {
  const q = props.query.toLowerCase();
  if (!q) return props.commands;
  return props.commands.filter(
    (cmd) =>
      cmd.name.toLowerCase().includes(q) ||
      cmd.description.toLowerCase().includes(q),
  );
});

// Group filtered commands by category
const groupedCommands = computed(() => {
  const groups = new Map<string, SlashCommand[]>();
  for (const cmd of filteredCommands.value) {
    const cat = cmd.category ?? "other";
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat)!.push(cmd);
  }
  return groups;
});

// Flat list for keyboard navigation
const flatList = computed(() => filteredCommands.value);

// Reset active index when query changes
watch(() => props.query, () => {
  activeIndex.value = 0;
});

// Scroll active item into view
watch(activeIndex, async () => {
  await nextTick();
  const el = listEl.value?.querySelector(".command-item.active");
  el?.scrollIntoView({ block: "nearest" });
});

function handleKeydown(e: KeyboardEvent) {
  if (!props.visible) return;

  if (e.key === "ArrowDown") {
    e.preventDefault();
    if (flatList.value.length > 0) {
      activeIndex.value = Math.min(activeIndex.value + 1, flatList.value.length - 1);
    }
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeIndex.value = Math.max(activeIndex.value - 1, 0);
  } else if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    const cmd = flatList.value[activeIndex.value];
    if (cmd) emit("select", cmd);
  } else if (e.key === "Escape") {
    e.preventDefault();
    emit("close");
  }
}

defineExpose({ handleKeydown });
</script>

<template>
  <div v-if="visible && flatList.length > 0" class="slash-palette" ref="listEl">
    <div v-for="[cat, cmds] in groupedCommands" :key="cat" class="command-group">
      <div class="group-label">{{ cat }}</div>
      <div
        v-for="cmd in cmds"
        :key="cmd.name"
        class="command-item"
        :class="{ active: flatList[activeIndex]?.name === cmd.name }"
        @click="emit('select', cmd)"
        @mouseenter="activeIndex = flatList.indexOf(cmd)"
      >
        <span class="cmd-name">{{ cmd.name }}</span>
        <span class="cmd-desc">{{ cmd.description }}</span>
        <span v-if="cmd.args?.length" class="cmd-args">
          {{ cmd.args.map(a => a.required ? `<${a.name}>` : `[${a.name}]`).join(" ") }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.slash-palette {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  max-height: 280px;
  overflow-y: auto;
  background: var(--bg-secondary);
  border: 1px solid var(--border-active);
  border-bottom: none;
  border-radius: var(--radius) var(--radius) 0 0;
  z-index: 100;
  padding: 4px 0;
}

.command-group {
  padding: 2px 0;
}

.command-group + .command-group {
  border-top: 1px solid var(--border);
}

.group-label {
  padding: 4px 12px 2px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  color: var(--text-muted);
  font-weight: 600;
}

.command-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 12px;
  cursor: pointer;
  font-size: 12px;
  line-height: 1.4;
}

.command-item:hover,
.command-item.active {
  background: rgba(0, 212, 255, 0.1);
}

.cmd-name {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--accent-main);
  min-width: 110px;
  flex-shrink: 0;
  font-size: 12px;
}

.cmd-desc {
  color: var(--text-secondary);
  flex: 1;
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cmd-args {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-muted);
  flex-shrink: 0;
}
</style>
