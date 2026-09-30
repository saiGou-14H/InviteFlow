<script setup lang="ts">
import { locale, t } from '../i18n'
</script>
<template>
  <a class="skip-link" href="#main">{{ t('skip') }}</a>
  <header class="topbar">
    <RouterLink class="brand" to="/" aria-label="InviteFlow"><span class="brand-mark" aria-hidden="true">IF</span>Invite<span>Flow</span></RouterLink>
    <nav :aria-label="t('navigation')">
      <RouterLink to="/">{{ t('userNav') }}</RouterLink>
      <RouterLink to="/admin">{{ t('adminNav') }}</RouterLink>
      <button class="language" type="button" :aria-label="locale === 'zh' ? 'Switch to English' : '切换中文'" @click="locale = locale === 'zh' ? 'en' : 'zh'">{{ locale === 'zh' ? 'EN' : '中文' }}</button>
    </nav>
  </header>
</template>
