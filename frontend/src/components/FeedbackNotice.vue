<script setup lang="ts">
import { t } from '../i18n'
import type { Notice } from '../composables/useFeedback'
defineProps<{ notice: Notice | null }>()
</script>
<template>
  <div class="feedback-region" aria-live="polite" aria-atomic="true">
    <div v-if="notice" :class="['notice', notice.kind]" :role="notice.kind === 'error' ? 'alert' : 'status'">
      <p>{{ t(notice.key) }}</p>
      <p v-if="notice.operationId" class="operation">{{ t('operationId') }}: <code>{{ notice.operationId }}</code></p>
    </div>
  </div>
</template>
