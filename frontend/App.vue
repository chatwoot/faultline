<template>
  <!-- Loading screen while /api/auth/me is in flight -->
  <div
    v-if="appLoading"
    class="flex items-center justify-center h-screen bg-white dark:bg-black"
  >
    <div class="flex flex-col items-center gap-4">
      <img src="/logo.svg" alt="Faultline" class="w-10 h-10 animate-pulse" />
    </div>
  </div>

  <div v-else class="flex h-screen bg-white dark:bg-black">
    <!-- Sidebar (only when authenticated and not on public route) -->
    <div
      v-if="showSidebar"
      :class="[
        'border-r border-gray-300 dark:border-gray-700 flex flex-col transition-all duration-200',
        sidebarCollapsed ? 'w-14' : 'w-56',
      ]"
    >
      <!-- Logo -->
      <div class="border-b border-gray-300 dark:border-gray-700 p-3">
        <div
          class="flex items-center gap-3"
          :class="sidebarCollapsed && 'justify-center'"
        >
          <img src="/logo.svg" alt="Faultline" class="w-8 h-8 shrink-0" />
          <div v-if="!sidebarCollapsed">
            <h1 class="text-base font-semibold">Faultline</h1>
            <p class="text-[11px] text-black/50 dark:text-white/50">
              Your Infra AI Agent
            </p>
          </div>
        </div>
      </div>

      <!-- Account Switcher -->
      <div class="border-b border-gray-300 dark:border-gray-700 p-2">
        <AccountSwitcher :collapsed="sidebarCollapsed" />
      </div>

      <!-- Nav -->
      <nav class="flex-1 p-2">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          :title="sidebarCollapsed ? item.label : undefined"
          :class="[
            'flex items-center gap-3 px-3 py-2 mb-1 text-sm rounded-sm transition-colors',
            sidebarCollapsed && 'justify-center',
            item.isActive()
              ? 'bg-black dark:bg-white text-white dark:text-black'
              : 'hover:bg-black/5 dark:hover:bg-white/5',
          ]"
        >
          <component :is="item.icon" class="w-4 h-4 shrink-0" />
          <span v-if="!sidebarCollapsed">{{ item.label }}</span>
        </RouterLink>
      </nav>

      <!-- Footer: User + Collapse -->
      <div class="border-t border-gray-300 dark:border-gray-700 p-2 space-y-1">
        <!-- User info + logout -->
        <div
          v-if="authStore.user"
          class="flex items-center gap-2 px-3 py-2"
          :class="sidebarCollapsed && 'justify-center'"
        >
          <img
            :src="authStore.user.avatarUrl"
            :alt="authStore.user.name"
            class="w-7 h-7 rounded-sm shrink-0"
          />
          <div v-if="!sidebarCollapsed" class="flex-1 min-w-0">
            <p class="text-xs truncate">{{ authStore.user.name }}</p>
            <p class="text-[10px] text-black/40 dark:text-white/40 truncate">
              {{ authStore.user.email }}
            </p>
          </div>
          <button
            v-if="!sidebarCollapsed"
            @click="handleLogout"
            title="Logout"
            class="text-black/30 dark:text-white/30 hover:text-black dark:hover:text-white shrink-0"
          >
            <ArrowRightOnRectangleIcon class="w-4 h-4" />
          </button>
        </div>

        <button
          @click="sidebarCollapsed = !sidebarCollapsed"
          :title="sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'"
          class="flex items-center gap-3 w-full px-3 py-2 text-black/40 dark:text-white/40 hover:text-black dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 transition-colors rounded-sm"
          :class="sidebarCollapsed && 'justify-center'"
        >
          <ChevronDoubleRightIcon
            v-if="sidebarCollapsed"
            class="w-4 h-4 shrink-0"
          />
          <ChevronDoubleLeftIcon v-else class="w-4 h-4 shrink-0" />
          <span v-if="!sidebarCollapsed" class="text-xs">Collapse</span>
        </button>
      </div>
    </div>

    <!-- Main content -->
    <div class="flex-1 overflow-hidden">
      <RouterView />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import {
  ChatBubbleLeftRightIcon,
  QueueListIcon,
  MapIcon,
  Cog6ToothIcon,
  ChevronDoubleLeftIcon,
  ChevronDoubleRightIcon,
  ArrowRightOnRectangleIcon,
} from "@heroicons/vue/24/outline";
import { useAuthStore } from "./stores/auth";
import { useAgentStore } from "./stores/agent";
import { useSocket } from "./composables/useSocket";
import AccountSwitcher from "./components/AccountSwitcher.vue";

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const agentStore = useAgentStore();
const {
  connect: socketConnect,
  disconnect: socketDisconnect,
  getConsumer,
} = useSocket();

const appLoading = ref(authStore.isAuthenticated);
const stored = localStorage.getItem("faultline:sidebar");
const sidebarCollapsed = ref(stored === "collapsed");

const publicPaths = [
  "/login",
  "/signup",
  "/verify-email",
  "/resend-verification",
];
const showSidebar = computed(() => {
  return (
    authStore.isAuthenticated &&
    !publicPaths.some((p) => route.path.startsWith(p))
  );
});

// Persist collapse state
watch(sidebarCollapsed, (v) => {
  localStorage.setItem("faultline:sidebar", v ? "collapsed" : "expanded");
});

// On mount: if authenticated, fetch user info and connect socket
onMounted(async () => {
  if (authStore.isAuthenticated) {
    try {
      await authStore.fetchCurrentUser();
      socketConnect(authStore.token!);
      listenForAccountEvents();
    } finally {
      appLoading.value = false;
    }
  }
});

function listenForAccountEvents() {
  const cable = getConsumer();
  if (!cable) return;

  cable.subscriptions.create(
    { channel: "AccountChannel" },
    {
      received(data: Record<string, unknown>) {
        const type = data.type as string;
        if (type === "conversation:created") {
          agentStore.addConversationToList(
            data as {
              id: string;
              title: string | null;
              createdAt: string;
              updatedAt: string;
              messageCount: number;
            },
          );
        } else if (type === "conversation:updated") {
          agentStore.updateConversationInList(
            data as { id: string; title?: string; updatedAt: string },
          );
        }
      },
    },
  );
}

async function handleLogout() {
  socketDisconnect();
  await authStore.logout();
  router.push("/login");
}

const navItems = [
  {
    to: "/chat",
    label: "Chat",
    icon: ChatBubbleLeftRightIcon,
    isActive: () => route.path.startsWith("/chat"),
  },
  {
    to: "/threads",
    label: "Threads",
    icon: QueueListIcon,
    isActive: () => route.path === "/threads",
  },
  {
    to: "/resource-maps",
    label: "Resources",
    icon: MapIcon,
    isActive: () => route.path.startsWith("/resource-maps"),
  },
  {
    to: "/settings",
    label: "Settings",
    icon: Cog6ToothIcon,
    isActive: () => route.path.startsWith("/settings"),
  },
];
</script>
