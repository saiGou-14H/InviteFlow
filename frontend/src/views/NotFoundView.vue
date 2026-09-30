<script setup lang="ts">
import { t } from '../i18n'
</script>
<template><section class="hero"><p class="eyebrow">404</p><h1>{{ t('notFound') }}</h1><RouterLink class="secondary inline-link" to="/">{{ t('backHome') }}</RouterLink></section></template>
