<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useAuth } from '../composables/useAuth'
import { useFeedback } from '../composables/useFeedback'
import { expiryToIso, isUuid, validQuantity } from '../utils/format'
import { t } from '../i18n'
import FeedbackNotice from '../components/FeedbackNotice.vue'

const { api, sessions } = useAuth()
const { busy, notice, run, error, info, accepted } = useFeedback()
const username = ref('')
const password = ref('')
const quantity = ref<number | string>(1)
const maxUses = ref<number | string>(1)
const expires = ref('')
const claimId = ref('')
const reason = ref('')
async function restore() {
  await run(async () => { const session = await api.restore('admin'); info(session ? 'restored' : 'noSession') })
}
async function login() {
  if (!username.value.trim() || username.value.trim().length > 128 || !password.value || password.value.length > 256) { error('invalidLogin'); return }
  await run(async () => {
    try { await api.login(username.value.trim(), password.value); info('adminLoggedIn') }
    finally { password.value = '' }
  })
}
async function logout() { await run(async () => { await api.logout('admin'); info('loggedOut') }) }
async function createBatch() {
  if (!sessions.admin) { error('unauthorized'); return }
  const count = Number(quantity.value)
  const uses = Number(maxUses.value)
  if (!validQuantity(count) || !validQuantity(uses)) { error('invalidQuantity'); return }
  let expiry: string | null
  try { expiry = expiryToIso(expires.value) } catch { error('invalidExpiry'); return }
  await run(async () => {
    accepted(await api.request('/admin/cdk-batches', {
      method: 'POST', role: 'admin', command: true,
      body: { quantity: count, max_uses: uses, expires_at: expiry },
    }))
  })
}
async function reconcile() {
  if (!sessions.admin) { error('unauthorized'); return }
  const id = claimId.value.trim()
  const explanation = reason.value.trim()
  if (!isUuid(id)) { error('invalidUuid'); return }
  if (!explanation || explanation.length > 500) { error('invalidReason'); return }
  await run(async () => {
    accepted(await api.request(`/admin/claims/${id}/reconcile`, { method: 'POST', role: 'admin', command: true, body: { reason: explanation } }))
  })
}
watch(() => sessions.admin, session => {
  if (!session) { password.value = ''; claimId.value = ''; reason.value = '' }
})
onMounted(() => { void restore() })
</script>
<template>
  <section class="hero"><p class="eyebrow">INVITEFLOW / {{ t('adminNav') }}</p><h1>{{ t('adminTitle') }}</h1><p class="hero-lead">{{ t('adminLead') }}</p></section>
  <FeedbackNotice :notice="notice" />
  <section v-if="!sessions.admin" class="card login-card">
    <div class="small-badge">ADMIN</div><h2>{{ t('loginTitle') }}</h2><p class="muted">{{ t('loginHint') }}</p>
    <form @submit.prevent="login" :aria-busy="busy">
      <label for="username">{{ t('username') }}</label><input id="username" v-model="username" autocomplete="username" maxlength="128" required :disabled="busy" />
      <label for="password">{{ t('password') }}</label><input id="password" v-model="password" type="password" autocomplete="current-password" maxlength="256" required :disabled="busy" />
      <button class="primary full-width" :disabled="busy">{{ busy ? t('working') : t('login') }}</button>
      <button type="button" class="text-button restore-button" :disabled="busy" @click="restore">{{ t('restore') }}</button>
    </form>
  </section>
  <template v-else>
    <section class="session-strip"><div><h2><span class="session-dot active" aria-hidden="true"></span>{{ t('adminReady') }}</h2><p class="muted">{{ t('restoreHint') }}</p></div><div class="actions"><button type="button" class="secondary" :disabled="busy" @click="restore">{{ t('restore') }}</button><button type="button" class="secondary" :disabled="busy" @click="logout">{{ t('logout') }}</button></div></section>
    <div class="workspace-grid">
      <section class="card accent-card"><div class="card-index" aria-hidden="true">01</div><h2>{{ t('cdkTitle') }}</h2><p class="muted">{{ t('cdkLead') }}</p>
        <form @submit.prevent="createBatch" :aria-busy="busy">
          <div class="form-grid"><div><label for="quantity">{{ t('quantity') }}</label><input id="quantity" v-model="quantity" type="number" min="1" max="1000" step="1" inputmode="numeric" required :disabled="busy" /></div><div><label for="max-uses">{{ t('maxUses') }}</label><input id="max-uses" v-model="maxUses" type="number" min="1" max="1000" step="1" inputmode="numeric" required :disabled="busy" /></div></div>
          <label for="expires">{{ t('expires') }}</label><input id="expires" v-model="expires" type="datetime-local" :disabled="busy" aria-describedby="expiry-hint" /><p id="expiry-hint" class="fine-print">{{ t('expiryHint') }}</p>
          <button class="primary full-width" :disabled="busy">{{ busy ? t('working') : t('createBatch') }}</button>
        </form>
      </section>
      <section class="card"><div class="card-index" aria-hidden="true">02</div><h2>{{ t('reconcileTitle') }}</h2><p class="muted">{{ t('reconcileLead') }}</p>
        <form @submit.prevent="reconcile" :aria-busy="busy">
          <label for="reconcile-claim">{{ t('claimId') }}</label><input id="reconcile-claim" v-model="claimId" maxlength="40" autocomplete="off" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" required :disabled="busy" />
          <label for="reason">{{ t('reason') }}</label><textarea id="reason" v-model="reason" rows="4" maxlength="500" :placeholder="t('reasonPlaceholder')" required :disabled="busy" autocomplete="off"></textarea>
          <button class="secondary full-width" :disabled="busy">{{ busy ? t('working') : t('reconcile') }}</button>
        </form>
      </section>
    </div>
  </template>
</template>
