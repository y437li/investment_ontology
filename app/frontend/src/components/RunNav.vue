<template>
  <header class="app-header">
    <div class="header-left">
      <div class="brand" @click="router.push('/')">THEME ENGINE</div>
      <div class="run-badge">{{ runId }}</div>
    </div>
    <nav class="view-nav">
      <router-link :to="`/runs/${runId}/import`" class="nav-link" active-class="active">Pipeline</router-link>
      <router-link :to="`/runs/${runId}/graph`" class="nav-link" active-class="active">Graph</router-link>
      <router-link :to="`/runs/${runId}/themes`" class="nav-link" active-class="active">Themes</router-link>
      <router-link :to="`/runs/${runId}/scenarios`" class="nav-link" active-class="active">Projections</router-link>
      <router-link :to="`/runs/${runId}/validation`" class="nav-link" active-class="active">Validation</router-link>
      <router-link :to="`/runs/${runId}/report`" class="nav-link" active-class="active">Report</router-link>
      <router-link :to="`/runs/${runId}/interaction`" class="nav-link" active-class="active">Evidence Q&amp;A</router-link>
    </nav>
  </header>
</template>

<script setup>
import { useRouter } from 'vue-router'
const props = defineProps({ runId: String })
const router = useRouter()
</script>

<style scoped>
.app-header {
  height: 56px;
  background: var(--black);
  color: var(--white);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 24px;
  flex-shrink: 0;
  gap: 20px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.brand {
  font-family: var(--font-mono);
  font-weight: 800;
  letter-spacing: 1px;
  cursor: pointer;
  font-size: 1rem;
}

.run-badge {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  color: #888;
  padding: 2px 8px;
  border: 1px solid #333;
  border-radius: 2px;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.view-nav {
  display: flex;
  gap: 4px;
}

.nav-link {
  color: #999;
  text-decoration: none;
  font-family: var(--font-mono);
  font-size: 0.78rem;
  padding: 6px 12px;
  border-radius: 2px;
  transition: all 0.2s;
}

.nav-link:hover,
.nav-link.active {
  color: var(--white);
  background: #222;
}
</style>
