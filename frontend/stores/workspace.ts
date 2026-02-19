import { defineStore } from 'pinia';
import { ref } from 'vue';
import axios from 'axios';
import { useAuthStore } from './auth';

export interface WorkspaceMember {
  id: string;
  userId: string;
  email: string;
  name: string;
  avatarUrl: string;
  role: string;
  joinedAt: string | null;
}

export interface PendingInvite {
  id: string;
  email: string;
  role: string;
  createdAt: string | null;
  expiresAt: string;
}

export const useWorkspaceStore = defineStore('workspace', () => {
  const members = ref<WorkspaceMember[]>([]);
  const pendingInvites = ref<PendingInvite[]>([]);
  const loading = ref(false);

  function accountBase() {
    const authStore = useAuthStore();
    return `/api/accounts/${authStore.currentAccount?.id}`;
  }

  async function fetchMembers() {
    loading.value = true;
    try {
      const { data } = await axios.get(`${accountBase()}/members`);
      members.value = data.members;
      pendingInvites.value = data.pendingInvites;
    } finally {
      loading.value = false;
    }
  }

  async function renameWorkspace(name: string): Promise<{ name: string; slug: string }> {
    const { data } = await axios.put(`${accountBase()}/rename`, { name });
    return data;
  }

  async function inviteMember(email: string, role: string) {
    const { data } = await axios.post(`${accountBase()}/invites`, { email, role });
    await fetchMembers();
    return data;
  }

  async function removeMember(userId: string) {
    await axios.delete(`${accountBase()}/members/${userId}`);
    await fetchMembers();
  }

  async function updateMemberRole(userId: string, role: string) {
    await axios.patch(`${accountBase()}/members/${userId}/role`, { role });
    await fetchMembers();
  }

  async function cancelInvite(inviteId: string) {
    await axios.delete(`${accountBase()}/invites/${inviteId}`);
    await fetchMembers();
  }

  async function acceptInvite(token: string): Promise<{ accountId: string; accountName: string }> {
    const { data } = await axios.post('/api/invites/accept', { token });
    return data;
  }

  return {
    members,
    pendingInvites,
    loading,
    fetchMembers,
    renameWorkspace,
    inviteMember,
    removeMember,
    updateMemberRole,
    cancelInvite,
    acceptInvite,
  };
});
