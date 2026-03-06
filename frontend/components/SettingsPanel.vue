<template>
  <div class="h-full overflow-y-auto">
    <div class="max-w-3xl mx-auto p-6">
      <div class="mb-6">
        <h1 class="text-lg font-semibold mb-2">Settings</h1>
        <p class="text-xs text-black/50 dark:text-white/50">Manage your workspace and configure integrations</p>
      </div>

      <!-- Tabs -->
      <div class="flex gap-0 border-b border-gray-300 dark:border-gray-700 mb-6">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="setTab(tab.id)"
          class="px-4 py-2 text-xs font-medium transition-colors -mb-px"
          :class="activeTab === tab.id
            ? 'border-b-2 border-black dark:border-white text-black dark:text-white'
            : 'text-black/40 dark:text-white/40 hover:text-black/70 dark:hover:text-white/70'"
        >
          {{ tab.label }}
        </button>
      </div>

      <!-- Account Tab -->
      <div v-if="activeTab === 'account'">
        <WorkspaceSettings />
      </div>

      <!-- Integrations Tab -->
      <div v-if="activeTab === 'integrations'">
        <!-- Integration Status -->
        <div class="mb-8 border border-gray-300 dark:border-gray-700 p-5">
          <h2 class="text-xs font-semibold mb-4 uppercase tracking-wide">Status</h2>
          <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div
              v-for="def in INTEGRATIONS"
              :key="def.id"
              class="flex items-center space-x-2"
            >
              <div
                :class="[
                  'w-2 h-2',
                  integrationStatus[def.id as keyof IntegrationStatus] ? 'bg-black dark:bg-white' : 'bg-black/20 dark:bg-white/20',
                ]"
              />
              <Icon
                :icon="def.icon"
                class="w-4 h-4 shrink-0"
                :class="integrationStatus[def.id as keyof IntegrationStatus] ? '' : 'text-black/20 dark:text-white/20'"
              />
              <span class="capitalize text-sm" :class="integrationStatus[def.id as keyof IntegrationStatus] ? '' : 'text-black/20 dark:text-white/20'">{{ def.id }}</span>
            </div>
          </div>
        </div>

        <!-- Integrations -->
        <div class="space-y-6">
          <div v-for="def in INTEGRATIONS" :key="def.id">
            <!-- Single-instance (OpenAI) -->
            <template v-if="!def.multiple">
              <SettingSection
                :title="def.title"
                :description="def.description"
                :connected="integrationStatus[def.id as keyof IntegrationStatus]"
                :saving="savingSection === def.id"
                :status="sectionStatus[def.id]"
                :status-error="sectionError[def.id]"
                @save="saveSection(def.id)"
              >
                <template v-for="field in def.fields" :key="field.key">
                  <div v-if="field.type === 'select'">
                    <label class="block text-xs font-medium mb-1.5">{{ field.label }}</label>
                    <select
                      :value="formData[`${def.id}.${field.key}`] || field.options?.[0]?.value"
                      @change="formData[`${def.id}.${field.key}`] = ($event.target as HTMLSelectElement).value"
                      class="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 focus:outline-none text-sm bg-transparent"
                    >
                      <option v-for="opt in field.options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                    </select>
                  </div>
                  <SettingInput
                    v-else
                    v-model="formData[`${def.id}.${field.key}`]"
                    :label="field.label"
                    :type="field.type"
                    :placeholder="field.placeholder"
                    :required="field.required"
                  />
                </template>
                <p v-if="def.hint" class="text-xs text-black/50 dark:text-white/50 mt-2">{{ def.hint }}</p>
              </SettingSection>
            </template>

            <!-- Multi-instance -->
            <template v-else>
              <div class="space-y-4">
                <div v-for="idx in getInstanceIndices(def.id)" :key="idx">
                  <SettingSection
                    :title="getInstanceIndices(def.id).length > 1 ? `${def.title} #${idx + 1}` : def.title"
                    :description="def.description"
                    :connected="instanceConnected(def, idx)"
                    :saving="savingSection === def.id"
                    :status="sectionStatus[def.id]"
                    :status-error="sectionError[def.id]"
                    @save="saveSection(def.id)"
                  >
                    <template v-for="field in def.fields" :key="field.key">
                      <div v-if="field.type === 'select'">
                        <label class="block text-xs font-medium mb-1.5">{{ field.label }}</label>
                        <select
                          :value="formData[`${def.id}.${idx}.${field.key}`] || field.options?.[0]?.value"
                          @change="formData[`${def.id}.${idx}.${field.key}`] = ($event.target as HTMLSelectElement).value"
                          class="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 focus:outline-none text-sm bg-transparent"
                        >
                          <option v-for="opt in field.options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                        </select>
                      </div>
                      <SettingInput
                        v-else
                        v-model="formData[`${def.id}.${idx}.${field.key}`]"
                        :label="field.label"
                        :type="field.type"
                        :placeholder="field.placeholder"
                        :required="field.required"
                      />
                    </template>
                    <p v-if="def.hint" class="text-xs text-black/50 dark:text-white/50 mt-2">{{ def.hint }}</p>

                    <!-- Webhook URL (PagerDuty) -->
                    <div v-if="getWebhookUrl(def.id, idx)" class="mt-3">
                      <label class="block text-xs font-medium mb-1.5">Webhook URL</label>
                      <div class="flex items-center gap-2">
                        <input
                          type="text"
                          :value="getWebhookUrl(def.id, idx)"
                          readonly
                          class="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-700 text-sm bg-black/5 dark:bg-white/5 text-black/70 dark:text-white/70 font-mono"
                        />
                        <button
                          type="button"
                          @click="copyWebhookUrl(def.id, idx)"
                          class="px-3 py-2 border border-gray-300 dark:border-gray-700 text-xs hover:bg-black/5 dark:hover:bg-white/5 transition-colors shrink-0"
                        >
                          {{ copiedWebhook === `${def.id}.${idx}` ? 'Copied!' : 'Copy' }}
                        </button>
                      </div>
                      <p class="text-xs text-black/40 dark:text-white/40 mt-1">Add this URL to PagerDuty → Integrations → Generic Webhooks (V3) to auto-investigate incidents.</p>
                    </div>

                    <!-- Remove button -->
                    <div class="flex justify-end mt-2" v-if="getInstanceIndices(def.id).length > 1">
                      <button
                        type="button"
                        @click="removeInstance(def.id, idx)"
                        class="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300"
                      >
                        Remove
                      </button>
                    </div>
                  </SettingSection>
                </div>

                <!-- Add instance button -->
                <button
                  type="button"
                  @click="addInstance(def.id)"
                  class="w-full py-2 border border-dashed border-gray-300 dark:border-gray-700 text-xs text-black/50 dark:text-white/50 hover:text-black dark:hover:text-white hover:border-gray-500 transition-colors"
                >
                  + Add another {{ def.title }} account
                </button>
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useAuthStore } from '../stores/auth';
import { useSettingsStore } from '../stores/settings';
import { Icon } from '@iconify/vue';
import SettingSection from './SettingSection.vue';
import SettingInput from './SettingInput.vue';
import WorkspaceSettings from './WorkspaceSettings.vue';
import type { Settings, IntegrationStatus, WebhookInfo } from '../types';

// ── Integration schema definitions ──

interface IntegrationField {
  key: string;
  label: string;
  type: 'text' | 'password' | 'select';
  placeholder?: string;
  required?: boolean;
  options?: { value: string; label: string }[];
}

interface IntegrationDef {
  id: string;
  title: string;
  description: string;
  icon: string;
  fields: IntegrationField[];
  multiple: boolean;
  hint?: string;
}

const INTEGRATIONS: IntegrationDef[] = [
  {
    id: 'openai',
    title: 'OpenAI',
    description: 'Required for AI agent functionality',
    icon: 'simple-icons:openai',
    multiple: false,
    fields: [
      { key: 'api_key', label: 'API Key', type: 'password', placeholder: 'sk-...' },
    ],
  },
  {
    id: 'newrelic',
    title: 'New Relic',
    description: 'APM monitoring - automatically discovers and monitors ALL applications in your account',
    icon: 'simple-icons:newrelic',
    multiple: true,
    fields: [
      { key: 'api_key', label: 'API Key', type: 'password', placeholder: 'NRAK-...' },
      { key: 'account_id', label: 'Account ID', type: 'text', placeholder: '1234567' },
      { key: 'region', label: 'Region', type: 'select', options: [{ value: 'us', label: 'US' }, { value: 'eu', label: 'EU' }] },
    ],
  },
  {
    id: 'sentry',
    title: 'Sentry',
    description: 'Error tracking - automatically discovers and monitors ALL projects in your organization',
    icon: 'simple-icons:sentry',
    multiple: true,
    fields: [
      { key: 'auth_token', label: 'Auth Token', type: 'password', placeholder: 'sntrys_...' },
      { key: 'org', label: 'Organization Slug', type: 'text', placeholder: 'my-org' },
    ],
  },
  {
    id: 'aws',
    title: 'AWS',
    description: 'Cloud infrastructure monitoring',
    icon: 'simple-icons:amazonaws',
    multiple: true,
    fields: [
      { key: 'access_key_id', label: 'Access Key ID', type: 'password', placeholder: 'AKIA...' },
      { key: 'secret_access_key', label: 'Secret Access Key', type: 'password', placeholder: '...' },
      { key: 'region', label: 'Default Region (optional)', type: 'text', placeholder: 'Auto-discovers all regions' },
    ],
  },
  {
    id: 'pagerduty',
    title: 'PagerDuty',
    description: 'Incident management — active incidents, on-call schedules, escalation policies',
    icon: 'simple-icons:pagerduty',
    multiple: true,
    hint: 'Generate at PagerDuty \u2192 My Profile \u2192 User Settings \u2192 API Access. Uses PagerDuty\'s hosted MCP service.',
    fields: [
      { key: 'api_key', label: 'User API Token', type: 'password', placeholder: 'u+...' },
    ],
  },
  {
    id: 'github',
    title: 'GitHub',
    description: 'Codebase analysis and code-level debugging',
    icon: 'simple-icons:github',
    multiple: true,
    fields: [
      { key: 'token', label: 'Personal Access Token', type: 'password', placeholder: 'ghp_...' },
      { key: 'owner', label: 'Repository Owner', type: 'text', placeholder: 'mycompany' },
      { key: 'repo', label: 'Repository Name', type: 'text', placeholder: 'myapp' },
    ],
  },
  {
    id: 'digitalocean',
    title: 'DigitalOcean',
    description: 'Cloud infrastructure — Droplets, managed databases, load balancers, Kubernetes, and metrics',
    icon: 'simple-icons:digitalocean',
    multiple: true,
    fields: [
      { key: 'api_token', label: 'API Token', type: 'password', placeholder: 'dop_v1_...' },
    ],
    hint: 'Generate at DigitalOcean \u2192 API \u2192 Tokens. Read-only scope is sufficient.',
  },
];

// ── Component state ──

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const settingsStore = useSettingsStore();

const tabs = [
  { id: 'account', label: 'Account' },
  { id: 'integrations', label: 'Integrations' },
] as const;

type TabId = (typeof tabs)[number]['id'];
const validTabs: readonly string[] = tabs.map((t) => t.id);

const activeTab = computed<TabId>(() => {
  const param = route.params.tab as string | undefined;
  return param && validTabs.includes(param) ? (param as TabId) : 'account';
});

function setTab(id: TabId) {
  router.replace(id === 'account' ? '/settings' : `/settings/${id}`);
}

const formData = ref<Settings>({});
const copiedWebhook = ref<string | null>(null);
const savingSection = ref<string | null>(null);
const sectionStatus = reactive<Record<string, string>>({});
const sectionError = reactive<Record<string, boolean>>({});

const integrationStatus = computed(() => settingsStore.integrationStatus);

watch(
  () => authStore.currentAccount?.id,
  async (id) => {
    if (id) {
      await settingsStore.fetchSettings();
      await settingsStore.fetchIntegrationStatus();
      formData.value = { ...settingsStore.settings };
    }
  },
  { immediate: true },
);

// ── Instance management utilities ──

function getInstanceIndices(integrationId: string): number[] {
  const prefix = `${integrationId}.`;
  const indices = new Set<number>();

  for (const key of Object.keys(formData.value)) {
    if (!key.startsWith(prefix)) continue;
    const rest = key.slice(prefix.length);
    const match = rest.match(/^(\d+)\./);
    if (match) {
      indices.add(parseInt(match[1], 10));
    }
  }

  const sorted = Array.from(indices).sort((a, b) => a - b);
  return sorted.length > 0 ? sorted : [0];
}

function addInstance(integrationId: string) {
  const indices = getInstanceIndices(integrationId);
  const nextIndex = indices.length > 0 ? Math.max(...indices) + 1 : 0;
  const def = INTEGRATIONS.find((d) => d.id === integrationId);
  if (!def) return;

  for (const field of def.fields) {
    formData.value[`${integrationId}.${nextIndex}.${field.key}`] = '';
  }
}

async function removeInstance(integrationId: string, index: number) {
  const indices = getInstanceIndices(integrationId);
  if (indices.length <= 1) return;

  const prefix = `${integrationId}.${index}.`;
  const hasSavedData = Object.keys(settingsStore.settings).some((k) => k.startsWith(prefix));

  if (hasSavedData) {
    await settingsStore.deleteIntegrationInstance(integrationId, index);
    formData.value = { ...settingsStore.settings };
  } else {
    const def = INTEGRATIONS.find((d) => d.id === integrationId);
    if (!def) return;

    for (const key of Object.keys(formData.value)) {
      if (key.startsWith(prefix)) {
        delete formData.value[key];
      }
    }

    for (const higherIdx of indices.filter((i) => i > index)) {
      for (const field of def.fields) {
        const oldKey = `${integrationId}.${higherIdx}.${field.key}`;
        const newKey = `${integrationId}.${higherIdx - 1}.${field.key}`;
        if (oldKey in formData.value) {
          formData.value[newKey] = formData.value[oldKey];
          delete formData.value[oldKey];
        }
      }
    }
  }
}

function instanceConnected(def: IntegrationDef, index: number): boolean {
  const requiredField = def.fields[0];
  if (!requiredField) return false;
  const key = `${def.id}.${index}.${requiredField.key}`;
  const val = settingsStore.settings[key];
  return !!val && val !== '';
}

function getWebhookUrl(integrationId: string, index: number): string | null {
  const webhook = settingsStore.webhooks.find(
    (w: WebhookInfo) => w.integration === integrationId && w.integrationIndex === index,
  );
  if (!webhook) return null;
  return `${window.location.origin}${webhook.url}`;
}

function copyWebhookUrl(integrationId: string, index: number) {
  const url = getWebhookUrl(integrationId, index);
  if (!url) return;
  navigator.clipboard.writeText(url);
  copiedWebhook.value = `${integrationId}.${index}`;
  setTimeout(() => {
    copiedWebhook.value = null;
  }, 2000);
}

// ── Save logic ──

async function saveSection(integrationId: string) {
  savingSection.value = integrationId;
  sectionStatus[integrationId] = '';
  sectionError[integrationId] = false;

  const def = INTEGRATIONS.find((d) => d.id === integrationId);
  if (!def) return;

  const payload: Settings = {};

  if (def.multiple) {
    const prefix = `${integrationId}.`;
    for (const [key, val] of Object.entries(formData.value)) {
      if (key.startsWith(prefix) && val !== undefined && val !== null && val !== '') {
        payload[key] = val;
      }
    }
  } else {
    for (const field of def.fields) {
      const key = `${integrationId}.${field.key}`;
      const val = formData.value[key];
      if (val !== undefined && val !== null && val !== '') {
        payload[key] = val;
      }
    }
  }

  const success = await settingsStore.updateSettings(payload);

  if (success) {
    sectionStatus[integrationId] = 'Saved';
    sectionError[integrationId] = false;
  } else {
    sectionStatus[integrationId] = settingsStore.error || 'Failed to save';
    sectionError[integrationId] = true;
  }

  savingSection.value = null;

  setTimeout(() => {
    sectionStatus[integrationId] = '';
    sectionError[integrationId] = false;
  }, 3000);
}
</script>
