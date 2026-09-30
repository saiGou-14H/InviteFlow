<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../composables/useAuth'
import type { Capabilities, ProbeState } from '../api'
import { t } from '../i18n'
const busy = ref(false)
const health = ref<ProbeState | null>(null)
const ready = ref<ProbeState | null>(null)
const capabilities = ref<Capabilities | null>(null)
const label = (value: ProbeState | null) => value === 'ok' ? t('available') : value === 'unavailable' ? t('unavailableStatus') : value === null ? t('checking') : t('unknownStatus')
async function refresh() {
  if (busy.value) return
  busy.value = true
  capabilities.value = null
  health.value = null
  ready.value = null
  try {
    const results = await Promise.all([api.probe('/healthz'), api.probe('/readyz'), api.capabilities().catch(() => null)])
    health.value = results[0]
    ready.value = results[1]
    capabilities.value = results[2]
  } finally { busy.value = false }
}
onMounted(() => { void refresh() })
</script>
<template>
  <aside class="service-panel" :aria-label="t('system')" :aria-busy="busy">
    <div class="section-heading"><h2>{{ t('system') }}</h2><button type="button" class="text-button" :disabled="busy" @click="refresh">{{ busy ? t('checking') : t('refreshStatus') }}</button></div>
    <dl class="status-grid" aria-live="polite">
      <div><dt>{{ t('health') }}</dt><dd :class="['status-value', health]">{{ label(health) }}</dd></div>
      <div><dt>{{ t('readiness') }}</dt><dd :class="['status-value', ready]">{{ label(ready) }}</dd></div>
      <div><dt>{{ t('capabilities') }}</dt><dd>{{ busy ? t('checking') : capabilities ? t('capabilityReady') : t('unknownStatus') }}</dd></div>
      <div v-if="capabilities"><dt>{{ t('implemented') }} / {{ t('reserved') }}</dt><dd>{{ capabilities.implemented.length }} / {{ capabilities.reserved_hooks.length }}</dd></div>
    </dl>
    <p class="fine-print">{{ t('healthHint') }}</p>
  </aside>
</template>
