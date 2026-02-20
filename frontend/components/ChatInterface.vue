<template>
  <div class="flex flex-col h-screen bg-white dark:bg-black">
    <!-- Header (only when there's an active thread) -->
    <div v-if="hasActiveThread" class="border-b border-gray-300 dark:border-gray-700 px-6 py-4">
      <div class="max-w-5xl mx-auto flex items-center justify-between">
        <h1 class="text-sm font-medium truncate mr-4">
          {{ conversationTitle }}
        </h1>
        <div class="flex items-center gap-2">
          <button
            @click="copyDebug"
            :disabled="messages.length === 0"
            class="shrink-0 flex items-center gap-1.5 px-3 py-2 border border-gray-300 dark:border-gray-700 hover:bg-black hover:text-white dark:hover:bg-white dark:hover:text-black transition-colors text-sm disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <ClipboardDocumentIcon v-if="!debugCopied" class="w-3.5 h-3.5" />
            <ClipboardDocumentCheckIcon v-else class="w-3.5 h-3.5 text-green-600 dark:text-green-400" />
            <span>{{ debugCopied ? 'Copied' : 'Copy Debug' }}</span>
          </button>
          <button
            @click="newConversation"
            class="shrink-0 px-4 py-2 border border-gray-300 dark:border-gray-700 hover:bg-black hover:text-white dark:hover:bg-white dark:hover:text-black transition-colors text-sm"
          >
            New Conversation
          </button>
        </div>
      </div>
    </div>

    <!-- Messages wrapper (relative for toast positioning) -->
    <div class="flex-1 relative overflow-hidden">
      <div
        ref="messagesContainer"
        @scroll="handleScroll"
        class="h-full overflow-y-auto px-6 py-4"
      >
        <WelcomeScreen
          v-if="!hasActiveThread"
          @use-example="useExample"
        />
        <MessageList v-if="messages.length > 0" :messages="messages" />

        <!-- Live agent activity (during streaming) -->
        <div v-if="isStreaming || isThinking" class="mt-4">
          <div class="max-w-5xl mx-auto w-full space-y-2">

            <!-- Thinking indicator (only when nothing else is showing yet) -->
            <div v-if="isThinking && activityStream.length === 0" class="flex items-center gap-2 text-sm text-black/50 dark:text-white/50">
              <span class="inline-flex gap-0.5">
                <span class="w-1 h-1 bg-black/40 dark:bg-white/40 rounded-full animate-pulse" />
                <span class="w-1 h-1 bg-black/40 dark:bg-white/40 rounded-full animate-pulse" style="animation-delay:150ms" />
                <span class="w-1 h-1 bg-black/40 dark:bg-white/40 rounded-full animate-pulse" style="animation-delay:300ms" />
              </span>
              <span>Investigating...</span>
            </div>

            <!-- Flat chronological activity stream -->
            <template v-for="(event, ei) in activityStream" :key="ei">
              <div v-if="event.type === 'tool_use'">
                <ToolUsageIndicator :tool-use="event.toolUse" />
              </div>
              <div v-else-if="event.type === 'reasoning'" class="border-l-2 border-black/15 dark:border-white/15 pl-3 py-1.5 text-xs text-black/70 dark:text-white/70">
                <div class="flex items-center gap-2 mb-1">
                  <span class="font-medium text-black/50 dark:text-white/50">Evaluation</span>
                  <span class="tabular-nums">{{ event.evaluation.confidence }}%</span>
                  <span class="uppercase tracking-wider text-[10px] text-black/40 dark:text-white/40">{{ event.evaluation.status }}</span>
                </div>
                <p class="leading-relaxed">{{ event.evaluation.summary }}</p>
                <div v-if="event.evaluation.nextSteps?.length > 0" class="mt-1 space-y-0.5">
                  <div v-for="(step, i) in event.evaluation.nextSteps" :key="i" class="text-black/50 dark:text-white/50">
                    &rarr; {{ step }}
                  </div>
                </div>
              </div>
            </template>

            <!-- Thinking indicator at the end so users know it's still working -->
            <div v-if="isAgentRunning && activityStream.length > 0" class="flex items-center gap-2 text-sm text-black/50 dark:text-white/50 pl-3">
              <span class="inline-flex gap-0.5">
                <span class="w-1 h-1 bg-black/40 dark:bg-white/40 rounded-full animate-pulse" />
                <span class="w-1 h-1 bg-black/40 dark:bg-white/40 rounded-full animate-pulse" style="animation-delay:150ms" />
                <span class="w-1 h-1 bg-black/40 dark:bg-white/40 rounded-full animate-pulse" style="animation-delay:300ms" />
              </span>
            </div>

          </div>
        </div>

        <!-- Error -->
        <div v-if="error" class="mt-4 p-4 border border-red-300 dark:border-red-800 text-sm">
          <p class="font-medium text-red-600 dark:text-red-400">Error</p>
          <p class="mt-1 text-red-600 dark:text-red-400">{{ error }}</p>
        </div>
      </div>

      <!-- New activity toast (Slack-style, above input) -->
      <transition
        enter-active-class="transition-all duration-200 ease-out"
        leave-active-class="transition-all duration-150 ease-in"
        enter-from-class="opacity-0 translate-y-2"
        enter-to-class="opacity-100 translate-y-0"
        leave-from-class="opacity-100 translate-y-0"
        leave-to-class="opacity-0 translate-y-2"
      >
        <button
          v-if="showNewActivityToast"
          @click="jumpToBottom"
          class="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1.5 px-3 py-1.5 bg-black dark:bg-white text-white dark:text-black text-xs font-medium shadow-lg hover:opacity-90 transition-opacity"
        >
          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 13.5L12 21m0 0l-7.5-7.5M12 21V3" />
          </svg>
          New activity
        </button>
      </transition>
    </div>

    <!-- Input -->
    <div :class="['border-t px-6 py-4', noteMode ? 'border-amber-300 dark:border-amber-700 bg-amber-50/50 dark:bg-amber-950/20' : 'border-gray-300 dark:border-gray-700']">
      <form @submit.prevent="noteMode ? sendNote() : sendMessage()" class="max-w-5xl mx-auto">
        <div class="flex items-start gap-3">
          <div class="flex-1 relative">
            <!-- Note textarea (hidden when not in note mode, but kept in DOM) -->
            <textarea
              v-show="noteMode"
              ref="noteTextarea"
              v-model="noteInput"
              @input="handleNoteInput"
              @keydown="handleNoteKeydown"
              placeholder="Add a private note... Use @ to mention"
              rows="2"
              class="w-full px-4 py-3 pr-10 border border-amber-300 dark:border-amber-600 focus:outline-none focus:border-amber-500 dark:focus:border-amber-400 resize-none text-sm bg-transparent transition-colors"
            />
            <!-- Message textarea (hidden when in note mode) -->
            <textarea
              v-show="!noteMode"
              v-model="inputMessage"
              @keydown.enter.exact.prevent="sendMessage()"
              placeholder="Ask about errors, performance, infrastructure..."
              rows="2"
              class="w-full px-4 py-3 pr-10 border border-gray-300 dark:border-gray-700 focus:outline-none focus:border-black dark:focus:border-white resize-none text-sm bg-transparent transition-colors"
              :disabled="isAgentRunning"
            />
            <button
              v-if="hasActiveThread"
              type="button"
              @click="noteMode = !noteMode"
              :title="noteMode ? 'Switch to message' : 'Switch to private note'"
              :class="[
                'absolute right-2 bottom-3 p-1 rounded transition-colors',
                noteMode
                  ? 'text-amber-500/60 hover:text-amber-600 dark:hover:text-amber-400'
                  : 'text-black/20 dark:text-white/20 hover:text-black/50 dark:hover:text-white/50',
              ]"
            >
              <LockClosedIcon class="w-3.5 h-3.5" />
            </button>
            <MentionAutocomplete
              v-if="noteMode"
              ref="mentionAutocomplete"
              :visible="mentionActive"
              :query="mentionQuery"
              @select="onMentionSelect"
              @dismiss="onMentionDismiss"
            />
          </div>
          <button
            v-if="!noteMode && isAgentRunning"
            type="button"
            @click="stopAgent"
            class="px-6 py-3 border border-red-400 dark:border-red-600 text-red-600 dark:text-red-400 hover:bg-red-600 hover:text-white dark:hover:bg-red-500 dark:hover:text-white text-sm transition-colors"
          >
            Stop
          </button>
          <button
            v-else-if="noteMode"
            type="submit"
            :disabled="!noteInput.trim()"
            class="px-6 py-3 bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-30 disabled:cursor-not-allowed text-sm transition-opacity"
          >
            Note
          </button>
          <button
            v-else
            type="submit"
            :disabled="!inputMessage.trim()"
            class="px-6 py-3 bg-black dark:bg-white text-white dark:text-black hover:bg-black/90 dark:hover:bg-white/90 disabled:opacity-30 disabled:cursor-not-allowed text-sm transition-opacity"
          >
            Send
          </button>
        </div>
        <div class="flex items-center justify-between mt-1.5">
          <div class="flex items-center gap-2">
            <p v-if="noteMode" class="text-[11px] text-amber-600/50 dark:text-amber-400/50">Private notes are visible to your team but not the AI agent</p>
            <p v-else class="text-[11px] text-black/30 dark:text-white/30">Enter to send, Shift+Enter for new line</p>
          </div>
          <select
            :class="{ invisible: noteMode }"
            :value="selectedModel"
            @change="setModel(($event.target as HTMLSelectElement).value)"
            :disabled="modelsLoading"
            class="text-[11px] text-black/50 dark:text-white/50 bg-transparent border border-gray-200 dark:border-gray-800 px-2 py-0.5 focus:outline-none focus:border-black dark:focus:border-white cursor-pointer disabled:opacity-40"
          >
            <option v-if="modelsLoading" disabled>Loading models...</option>
            <option
              v-for="m in availableModels"
              :key="m.id"
              :value="m.id"
              class="bg-white dark:bg-black"
            >
              {{ m.label }}
            </option>
          </select>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import { LockClosedIcon } from '@heroicons/vue/24/solid';
import { ClipboardDocumentIcon, ClipboardDocumentCheckIcon } from '@heroicons/vue/24/outline';
import { useAgent } from '../composables/useAgent';
import { useAgentStore } from '../stores/agent';
import type { ToolUse, EvaluationResult } from '../types';
import MessageList from './MessageList.vue';
import WelcomeScreen from './WelcomeScreen.vue';
import ToolUsageIndicator from './ToolUsageIndicator.vue';
import MentionAutocomplete from './MentionAutocomplete.vue';

const router = useRouter();
const messagesContainer = ref<HTMLElement | null>(null);
const agentStore = useAgentStore();

const activityStream = computed(() => agentStore.activityStream);

const {
  inputMessage,
  noteInput,
  noteMode,
  pendingMentions,
  messages,
  isThinking,
  isStreaming,
  error,
  selectedModel,
  availableModels,
  modelsLoading,
  setModel,
  fetchModels,
  sendMessage,
  sendNote,
  stopAgent,
} = useAgent();

// ── Mention autocomplete ─────────────────────────────────────────
const mentionActive = ref(false);
const mentionQuery = ref('');
const mentionStartIndex = ref(-1);
const noteTextarea = ref<HTMLTextAreaElement | null>(null);
const mentionAutocomplete = ref<InstanceType<typeof MentionAutocomplete> | null>(null);

function handleNoteInput(e: Event) {
  const textarea = e.target as HTMLTextAreaElement;
  const cursorPos = textarea.selectionStart;
  const text = textarea.value;

  // Look backward from cursor for an unmatched '@'
  const beforeCursor = text.slice(0, cursorPos);
  const atIndex = beforeCursor.lastIndexOf('@');

  if (atIndex >= 0) {
    // '@' must be at start or preceded by a space/newline
    const charBefore = atIndex > 0 ? beforeCursor[atIndex - 1] : ' ';
    const textAfterAt = beforeCursor.slice(atIndex + 1);
    // No spaces yet in the query (single-word matching while typing)
    if ((charBefore === ' ' || charBefore === '\n' || atIndex === 0) && !/\s/.test(textAfterAt)) {
      mentionActive.value = true;
      mentionQuery.value = textAfterAt;
      mentionStartIndex.value = atIndex;
      return;
    }
  }

  mentionActive.value = false;
  mentionQuery.value = '';
}

function handleNoteKeydown(e: KeyboardEvent) {
  if (!mentionActive.value) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendNote();
    }
    return;
  }

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    mentionAutocomplete.value?.moveHighlight(1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    mentionAutocomplete.value?.moveHighlight(-1);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    mentionAutocomplete.value?.selectHighlighted();
  } else if (e.key === 'Escape') {
    e.preventDefault();
    mentionActive.value = false;
  }
}

function onMentionSelect(member: { userId: string; name: string }) {
  const before = noteInput.value.slice(0, mentionStartIndex.value);
  const after = noteInput.value.slice(mentionStartIndex.value + 1 + mentionQuery.value.length);
  noteInput.value = `${before}@${member.name} ${after}`;
  pendingMentions.value.push(member);
  mentionActive.value = false;
  mentionQuery.value = '';

  // Restore focus and cursor position
  nextTick(() => {
    const textarea = noteTextarea.value;
    if (textarea) {
      const newPos = before.length + 1 + member.name.length + 1;
      textarea.focus();
      textarea.setSelectionRange(newPos, newPos);
    }
  });
}

function onMentionDismiss() {
  mentionActive.value = false;
  mentionQuery.value = '';
}

const isAgentRunning = computed(() => isThinking.value || isStreaming.value);

const hasActiveThread = computed(() =>
  messages.value.length > 0 || isThinking.value || isStreaming.value
);

const conversationTitle = computed(() =>
  agentStore.currentConversation?.title || 'New Conversation'
);

// ── Debug copy ──────────────────────────────────────────────────
const debugCopied = ref(false);

function formatToolUse(tu: ToolUse): string {
  const lines: string[] = [];
  lines.push(`Tool: ${tu.name}`);
  if (tu.integration) lines.push(`Integration: ${tu.integration}`);
  if (tu.agentId) lines.push(`Agent: ${tu.agentId}`);
  lines.push(`Status: ${tu.status}${tu.executionTimeMs != null ? ` (${tu.executionTimeMs}ms)` : ''}`);
  if (tu.input && Object.keys(tu.input).length > 0) {
    lines.push(`Input: ${JSON.stringify(tu.input, null, 2)}`);
  }
  if (tu.error) {
    lines.push(`Error: ${tu.error}`);
  }
  if (tu.output != null) {
    const outputStr = typeof tu.output === 'string' ? tu.output : JSON.stringify(tu.output, null, 2);
    lines.push(`Output: ${outputStr}`);
  }
  return lines.join('\n');
}

function formatEvaluation(ev: { iteration: number; evaluation: EvaluationResult }): string {
  const e = ev.evaluation;
  const lines = [
    `Iteration: ${ev.iteration}`,
    `Status: ${e.status} | Confidence: ${e.confidence}%`,
    `Summary: ${e.summary}`,
  ];
  if (e.nextSteps?.length > 0) {
    lines.push('Next Steps:');
    for (const step of e.nextSteps) {
      lines.push(`  → ${step}`);
    }
  }
  return lines.join('\n');
}

function buildDebugDump(): string {
  const conv = agentStore.currentConversation;
  if (!conv) return '';

  const sections: string[] = [];
  const divider = '─'.repeat(60);

  // Header
  sections.push([
    `# Debug Dump: ${conv.title || 'Untitled Conversation'}`,
    `Conversation ID: ${conv.id}`,
    `Created: ${conv.createdAt}`,
    `Messages: ${conv.messages.length}`,
  ].join('\n'));

  // Messages
  for (const msg of conv.messages) {
    const ts = new Date(msg.createdAt).toISOString();

    if (msg.messageType === 'tool_use' && msg.toolUses) {
      sections.push([
        divider,
        `## [TOOL] ${ts}`,
        formatToolUse(msg.toolUses),
      ].join('\n'));
    } else if (msg.messageType === 'evaluation' && msg.reasoning) {
      sections.push([
        divider,
        `## [EVALUATION] ${ts}`,
        formatEvaluation(msg.reasoning),
      ].join('\n'));
    } else if (msg.messageType === 'note') {
      const mentionStr = msg.mentions?.length
        ? `\nMentions: ${msg.mentions.map(m => `@${m.name}`).join(', ')}`
        : '';
      sections.push([
        divider,
        `## [NOTE] ${ts}`,
        msg.content,
        mentionStr,
      ].filter(Boolean).join('\n'));
    } else if (msg.role === 'user') {
      sections.push([
        divider,
        `## [USER] ${ts}`,
        msg.content,
      ].join('\n'));
    } else if (msg.role === 'assistant') {
      sections.push([
        divider,
        `## [ASSISTANT] ${ts}`,
        msg.content,
      ].join('\n'));
    }
  }

  return sections.join('\n\n');
}

async function copyDebug() {
  const dump = buildDebugDump();
  if (!dump) return;

  try {
    await navigator.clipboard.writeText(dump);
    debugCopied.value = true;
    setTimeout(() => { debugCopied.value = false; }, 1500);
  } catch {
    // Fallback for non-secure contexts
    const textarea = document.createElement('textarea');
    textarea.value = dump;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
    debugCopied.value = true;
    setTimeout(() => { debugCopied.value = false; }, 1500);
  }
}

// ── Scroll tracking ──────────────────────────────────────────────
const isNearBottom = ref(true);
const showNewActivityToast = ref(false);
const SCROLL_THRESHOLD = 120; // px from bottom to count as "at bottom"

function handleScroll() {
  if (!messagesContainer.value) return;
  const { scrollTop, scrollHeight, clientHeight } = messagesContainer.value;
  const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
  isNearBottom.value = distanceFromBottom <= SCROLL_THRESHOLD;

  // User scrolled back to bottom — dismiss toast
  if (isNearBottom.value) {
    showNewActivityToast.value = false;
  }
}

/** Called by watchers when new content arrives. Scrolls only if user hasn't scrolled up. */
function autoScroll() {
  if (!isNearBottom.value) {
    // User scrolled up — show toast instead
    if (isAgentRunning.value) {
      showNewActivityToast.value = true;
    }
    return;
  }
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
    }
  });
}

/** User-initiated scroll (toast click, send message). Always scrolls. */
function jumpToBottom() {
  showNewActivityToast.value = false;
  isNearBottom.value = true;
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTo({
        top: messagesContainer.value.scrollHeight,
        behavior: 'smooth',
      });
    }
  });
}

// ── Misc ─────────────────────────────────────────────────────────

function newConversation() {
  agentStore.createNewConversation();
  router.push('/chat');
}

function useExample(example: string) {
  inputMessage.value = example;
  sendMessage();
}

// ── Lifecycle & watchers ─────────────────────────────────────────

onMounted(() => {
  fetchModels();
});

// Dismiss toast when agent finishes
watch(isAgentRunning, (running) => {
  if (!running) {
    showNewActivityToast.value = false;
  }
});

// When user sends a message, always jump to bottom
watch(messages, (newVal, oldVal) => {
  const lastMsg = newVal[newVal.length - 1];
  if (lastMsg?.role === 'user' && newVal.length > (oldVal?.length ?? 0)) {
    // User just sent a message — force scroll
    jumpToBottom();
  } else {
    autoScroll();
  }
}, { deep: true });

watch([isStreaming, isThinking], () => autoScroll());
watch(activityStream, () => autoScroll(), { deep: true });
</script>
