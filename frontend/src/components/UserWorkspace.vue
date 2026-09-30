<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useAuth } from '../composables/useAuth'
import { useFeedback } from '../composables/useFeedback'
import { isUuid, parseCodes, safeSnapshot, type ClaimStatus } from '../utils/format'
import { t } from '../i18n'
import FeedbackNotice from './FeedbackNotice.vue'

const { sessions, api } = useAuth()
const { busy, notice, run, error, info, accepted } = useFeedback()
const codes = ref('')
const claimId = ref('')
const snapshot = ref<{ id: string; status: ClaimStatus | 'unknown' } | null>(null)
const parsed = computed(() => parseCodes(codes.value))
const authenticated = computed(() => !!sessions.user)

async function restore() {
  await run(async () => { const session = await api.restore('user'); info(session ? 'restored' : 'noSession') })
}
async function createSession() {
  await run(async () => { await api.createUserSession(); info('sessionCreated') })
}
async function logout() {
  await run(async () => { await api.logout('user'); info('loggedOut') })
}
async function submit() {
  if (!authenticated.value) { error('unauthorized'); return }
  if (!parsed.value.length || parsed.value.length > 100) { error('invalidCodes'); return }
  await run(async () => {
    const result = await api.request('/claims/batches', { method: 'POST', body: { codes: parsed.value }, role: 'user', command: true })
    accepted(result)
    codes.value = ''
  })
}
async function lookup() {
  snapshot.value = null
  if (!authenticated.value) { error('unauthorized'); return }
  const id = claimId.value.trim()
  if (!isUuid(id)) { error('invalidUuid'); return }
  await run(async () => {
    const result = await api.request(`/claims/${id}`, { role: 'user' })
    snapshot.value = { id, ...safeSnapshot(result.payload) }
    info('snapshotLoaded')
  })
}
watch(() => sessions.user, session => {
  if (!session) { codes.value = ''; claimId.value = ''; snapshot.value = null }
})
watch(claimId, () => { snapshot.value = null })
onMounted(() => { void restore() })
</script>
<template>
  <section class="hero">
    <p class="eyebrow">INVITEFLOW / {{ t('userNav') }}</p>
    <h1>{{ t('userTitle') }}</h1>
    <p class="hero-lead">{{ t('userLead') }}</p>
  </section>
  <section class="session-strip" :aria-label="t('session')" :aria-busy="busy">
    <div><h2><span class="session-dot" :class="{ active: authenticated }" aria-hidden="true"></span>{{ authenticated ? t('sessionReady') : t('sessionEmpty') }}</h2><p class="muted">{{ t('restoreHint') }}</p></div>
    <div class="actions">
      <button v-if="!authenticated" type="button" class="primary" :disabled="busy" @click="createSession">{{ t('createSession') }}</button>
      <button type="button" class="secondary" :disabled="busy" @click="restore">{{ t('restore') }}</button>
      <button v-if="authenticated" type="button" class="secondary" :disabled="busy" @click="logout">{{ t('logout') }}</button>
    </div>
  </section>
  <FeedbackNotice :notice="notice" />
  <div class="workspace-grid">
    <section class="card accent-card">
      <div class="card-index" aria-hidden="true">01</div>
      <h2>{{ t('codeTitle') }}</h2><p class="muted">{{ t('codeLead') }}</p>
      <form @submit.prevent="submit" :aria-busy="busy">
        <label for="invite-codes">{{ t('codesLabel') }}</label>
        <textarea id="invite-codes" v-model="codes" rows="7" :placeholder="t('codesPlaceholder')" spellcheck="false" autocomplete="off" autocapitalize="off" :disabled="busy" aria-describedby="codes-hint"></textarea>
        <div class="field-meta"><span>{{ t('codeCount') }}: {{ parsed.length }}</span><button type="button" class="text-button" :disabled="busy || !codes" @click="codes = ''">{{ t('clearCodes') }}</button></div>
        <p id="codes-hint" class="fine-print">{{ t('codeLimit') }}</p>
        <button class="primary full-width" :disabled="busy || !authenticated">{{ busy ? t('working') : t('submitCodes') }} <span aria-hidden="true">↗</span></button>
        <p v-if="!authenticated" class="fine-print">{{ t('needSession') }}</p>
      </form>
    </section>
    <section class="card">
      <div class="card-index" aria-hidden="true">02</div>
      <h2>{{ t('lookupTitle') }}</h2><p class="muted">{{ t('lookupLead') }}</p>
      <form @submit.prevent="lookup" :aria-busy="busy">
        <label for="claim-id">{{ t('claimId') }}</label>
        <input id="claim-id" v-model="claimId" type="text" maxlength="40" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" autocomplete="off" spellcheck="false" :disabled="busy" />
        <button class="secondary full-width" :disabled="busy || !authenticated">{{ busy ? t('working') : t('lookup') }}</button>
      </form>
      <dl v-if="snapshot" class="snapshot" aria-live="polite"><dt>{{ t('claimId') }}</dt><dd><code>{{ snapshot.id }}</code></dd><dt>{{ t('claimStatus') }}</dt><dd>{{ t(snapshot.status) }}</dd></dl>
      <div class="placeholder-block">
        <span class="small-badge">{{ t('pendingBusiness') }}</span>
        <div class="future-actions"><button type="button" disabled aria-describedby="action-hint">{{ t('confirm') }}</button><button type="button" disabled aria-describedby="action-hint">{{ t('retry') }}</button><button type="button" disabled aria-describedby="action-hint">{{ t('followup') }}</button></div>
        <p id="action-hint" class="fine-print">{{ t('actionPlaceholder') }}</p>
      </div>
    </section>
  </div>
</template>
