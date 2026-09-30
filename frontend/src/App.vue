<script setup lang="ts">
import AppHeader from './components/AppHeader.vue'
import ServiceStatus from './components/ServiceStatus.vue'
import { t } from './i18n'
</script>
<template>
  <div class="app-shell">
    <AppHeader />
    <main id="main" tabindex="-1">
      <div class="intro-row"><span class="foundation-badge"><span aria-hidden="true">◈</span> {{ t('badge') }}</span><span class="tagline">{{ t('subtitle') }}</span></div>
      <RouterView />
      <ServiceStatus />
      <p class="security-note">{{ t('security') }}</p>
    </main>
    <footer><span>InviteFlow</span><p>{{ t('footer') }}</p></footer>
  </div>
</template>
