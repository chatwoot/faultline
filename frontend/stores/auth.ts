import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  avatarUrl: string;
}

export interface AuthAccount {
  id: string;
  name: string;
  slug: string;
  role?: string;
}

export interface PendingWorkspaceInvite {
  id: string;
  token: string;
  accountId: string;
  accountName: string;
  role: string;
}

const TOKEN_KEY = 'faultline:token';

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null);
  const currentAccount = ref<AuthAccount | null>(null);
  const accounts = ref<AuthAccount[]>([]);
  const pendingInvites = ref<PendingWorkspaceInvite[]>([]);
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY));

  const isAuthenticated = computed(() => !!token.value);

  function setToken(t: string) {
    token.value = t;
    localStorage.setItem(TOKEN_KEY, t);
  }

  function clearAuth() {
    user.value = null;
    currentAccount.value = null;
    accounts.value = [];
    pendingInvites.value = [];
    token.value = null;
    localStorage.removeItem(TOKEN_KEY);
  }

  async function signup(email: string, password: string, name: string): Promise<{ autoConfirmed: boolean; message: string }> {
    const { data } = await axios.post('/api/auth/signup', { email, password, name });
    return data;
  }

  async function login(email: string, password: string): Promise<void> {
    const { data } = await axios.post('/api/auth/login', { email, password });
    setToken(data.token);
    user.value = data.user;
    currentAccount.value = data.account;
  }

  async function logout(): Promise<void> {
    try {
      await axios.post('/api/auth/logout');
    } catch {
      // Ignore errors — we clear local state regardless
    }
    clearAuth();
  }

  async function fetchCurrentUser(): Promise<void> {
    try {
      const { data } = await axios.get('/api/auth/me');
      user.value = data.user;
      currentAccount.value = data.account;
      accounts.value = data.accounts;
      pendingInvites.value = data.pendingInvites || [];
    } catch {
      clearAuth();
    }
  }

  async function switchAccount(accountId: string): Promise<void> {
    const { data } = await axios.post('/api/auth/switch-account', { accountId });
    setToken(data.token);
    currentAccount.value = data.account;
  }

  return {
    user,
    currentAccount,
    accounts,
    pendingInvites,
    token,
    isAuthenticated,
    setToken,
    clearAuth,
    signup,
    login,
    logout,
    fetchCurrentUser,
    switchAccount,
  };
});
