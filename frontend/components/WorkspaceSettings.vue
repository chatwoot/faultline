<template>
  <div class="space-y-6 mb-8">
    <!-- Workspace Name -->
    <div class="border border-gray-300 dark:border-gray-700 p-5">
      <div class="mb-4">
        <h3 class="text-xs font-semibold uppercase tracking-wide">Workspace</h3>
        <p class="text-xs text-black/50 dark:text-white/50 mt-1">Manage workspace name, members, and invitations</p>
      </div>

      <div class="space-y-3">
        <div>
          <label class="block text-xs font-medium mb-1.5">Workspace Name</label>
          <div class="flex gap-2">
            <input
              v-model="workspaceName"
              type="text"
              :disabled="!canManage"
              class="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-700 focus:outline-none text-sm bg-transparent disabled:opacity-50"
            />
            <button
              v-if="canManage"
              @click="handleRename"
              :disabled="renaming || workspaceName === authStore.currentAccount?.name"
              class="px-4 py-2 bg-black dark:bg-white text-white dark:text-black text-xs hover:bg-black/90 dark:hover:bg-white/90 disabled:opacity-30 disabled:cursor-not-allowed"
            >
              {{ renaming ? 'Saving...' : 'Save' }}
            </button>
          </div>
          <p v-if="renameStatus" class="text-xs mt-1" :class="renameError ? 'text-red-600 dark:text-red-400' : 'text-black/50 dark:text-white/50'">
            {{ renameStatus }}
          </p>
        </div>
      </div>
    </div>

    <!-- Members -->
    <div class="border border-gray-300 dark:border-gray-700 p-5">
      <div class="mb-4">
        <h3 class="text-xs font-semibold uppercase tracking-wide">Members</h3>
      </div>

      <div v-if="workspaceStore.loading" class="text-xs text-black/50 dark:text-white/50">Loading...</div>

      <div v-else class="space-y-2">
        <div
          v-for="member in workspaceStore.members"
          :key="member.userId"
          class="flex items-center justify-between py-2 border-b border-gray-200 dark:border-gray-800 last:border-0"
        >
          <div class="flex items-center gap-3">
            <img
              :src="member.avatarUrl"
              :alt="member.name"
              class="w-7 h-7 rounded-sm shrink-0"
            />
            <div>
              <div class="text-sm">{{ member.name }}</div>
              <div class="text-xs text-black/50 dark:text-white/50">{{ member.email }}</div>
            </div>
          </div>

          <div class="flex items-center gap-2">
            <!-- Role badge / dropdown -->
            <div v-if="isOwner && member.userId !== authStore.user?.id" class="relative">
              <select
                :value="member.role"
                @change="handleRoleChange(member.userId, ($event.target as HTMLSelectElement).value)"
                class="text-xs border border-gray-300 dark:border-gray-700 bg-transparent pl-2 pr-5 py-1 focus:outline-none appearance-none cursor-pointer"
              >
                <option value="owner">owner</option>
                <option value="admin">admin</option>
                <option value="member">member</option>
              </select>
              <ChevronDownIcon class="w-3 h-3 absolute right-1 top-1/2 -translate-y-1/2 pointer-events-none text-black/40 dark:text-white/40" />
            </div>
            <span v-else class="text-xs px-2 py-0.5 bg-black/5 dark:bg-white/5 border border-gray-300 dark:border-gray-700">
              {{ member.role }}
            </span>

            <!-- Remove button -->
            <button
              v-if="canManage && member.userId !== authStore.user?.id"
              @click="handleRemoveMember(member.userId, member.name)"
              class="text-xs text-black/40 dark:text-white/40 hover:text-red-600 dark:hover:text-red-400 px-1"
              title="Remove member"
            >
              &times;
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Pending Invites -->
    <div v-if="workspaceStore.pendingInvites.length > 0" class="border border-gray-300 dark:border-gray-700 p-5">
      <div class="mb-4">
        <h3 class="text-xs font-semibold uppercase tracking-wide">Pending Invites</h3>
      </div>

      <div class="space-y-2">
        <div
          v-for="invite in workspaceStore.pendingInvites"
          :key="invite.id"
          class="flex items-center justify-between py-2 border-b border-gray-200 dark:border-gray-800 last:border-0"
        >
          <div>
            <div class="text-sm">{{ invite.email }}</div>
            <div class="text-xs text-black/50 dark:text-white/50">Role: {{ invite.role }}</div>
          </div>
          <button
            v-if="canManage"
            @click="handleCancelInvite(invite.id)"
            class="text-xs text-black/40 dark:text-white/40 hover:text-red-600 dark:hover:text-red-400 px-2 py-1"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>

    <!-- Invite Form -->
    <div v-if="canManage" class="border border-gray-300 dark:border-gray-700 p-5">
      <div class="mb-4">
        <h3 class="text-xs font-semibold uppercase tracking-wide">Invite Member</h3>
      </div>

      <div class="flex gap-2">
        <input
          v-model="inviteEmail"
          type="email"
          placeholder="colleague@company.com"
          class="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-700 focus:outline-none text-sm bg-transparent"
        />
        <div class="relative">
          <select
            v-model="inviteRole"
            class="pl-3 pr-7 py-2 border border-gray-300 dark:border-gray-700 bg-transparent text-sm focus:outline-none appearance-none cursor-pointer"
          >
            <option value="member">member</option>
            <option value="admin">admin</option>
          </select>
          <ChevronDownIcon class="w-3.5 h-3.5 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-black/40 dark:text-white/40" />
        </div>
        <button
          @click="handleInvite"
          :disabled="inviting || !inviteEmail"
          class="px-4 py-2 bg-black dark:bg-white text-white dark:text-black text-xs hover:bg-black/90 dark:hover:bg-white/90 disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
        >
          {{ inviting ? 'Inviting...' : 'Invite' }}
        </button>
      </div>
      <p v-if="inviteStatus" class="text-xs mt-2" :class="inviteError ? 'text-red-600 dark:text-red-400' : 'text-black/50 dark:text-white/50'">
        {{ inviteStatus }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { ChevronDownIcon } from '@heroicons/vue/16/solid';
import { useAuthStore } from '../stores/auth';
import { useWorkspaceStore } from '../stores/workspace';

const authStore = useAuthStore();
const workspaceStore = useWorkspaceStore();

const workspaceName = ref('');
const renaming = ref(false);
const renameStatus = ref('');
const renameError = ref(false);

const inviteEmail = ref('');
const inviteRole = ref('member');
const inviting = ref(false);
const inviteStatus = ref('');
const inviteError = ref(false);

const currentRole = computed(() => authStore.currentAccount?.role);
const isOwner = computed(() => currentRole.value === 'owner');
const canManage = computed(() => currentRole.value === 'owner' || currentRole.value === 'admin');

// Wait for currentAccount to be loaded (after refresh) before fetching
watch(
  () => authStore.currentAccount?.id,
  (id) => {
    if (id) {
      workspaceName.value = authStore.currentAccount?.name || '';
      workspaceStore.fetchMembers();
    }
  },
  { immediate: true },
);

async function handleRename() {
  renaming.value = true;
  renameStatus.value = '';
  renameError.value = false;

  try {
    const result = await workspaceStore.renameWorkspace(workspaceName.value);
    if (authStore.currentAccount) {
      authStore.currentAccount.name = result.name;
      authStore.currentAccount.slug = result.slug;
    }
    renameStatus.value = 'Saved';
  } catch (err: any) {
    renameStatus.value = err.response?.data?.error || 'Failed to rename';
    renameError.value = true;
  } finally {
    renaming.value = false;
    setTimeout(() => { renameStatus.value = ''; renameError.value = false; }, 3000);
  }
}

async function handleInvite() {
  inviting.value = true;
  inviteStatus.value = '';
  inviteError.value = false;

  try {
    await workspaceStore.inviteMember(inviteEmail.value, inviteRole.value);
    inviteStatus.value = `Invite sent to ${inviteEmail.value}`;
    inviteEmail.value = '';
    inviteRole.value = 'member';
  } catch (err: any) {
    inviteStatus.value = err.response?.data?.error || 'Failed to send invite';
    inviteError.value = true;
  } finally {
    inviting.value = false;
    setTimeout(() => { inviteStatus.value = ''; inviteError.value = false; }, 3000);
  }
}

async function handleRemoveMember(userId: string, name: string) {
  if (!confirm(`Remove ${name} from this workspace?`)) return;
  try {
    await workspaceStore.removeMember(userId);
  } catch (err: any) {
    alert(err.response?.data?.error || 'Failed to remove member');
  }
}

async function handleCancelInvite(inviteId: string) {
  try {
    await workspaceStore.cancelInvite(inviteId);
  } catch (err: any) {
    alert(err.response?.data?.error || 'Failed to cancel invite');
  }
}

async function handleRoleChange(userId: string, newRole: string) {
  try {
    await workspaceStore.updateMemberRole(userId, newRole);
  } catch (err: any) {
    alert(err.response?.data?.error || 'Failed to update role');
    await workspaceStore.fetchMembers();
  }
}
</script>
