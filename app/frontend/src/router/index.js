import { createRouter, createWebHistory } from 'vue-router'
import Home from '../views/Home.vue'
import ImportView from '../views/ImportView.vue'
import GraphView from '../views/GraphView.vue'
import ThemesView from '../views/ThemesView.vue'
import ValidationView from '../views/ValidationView.vue'
import ReportView from '../views/ReportView.vue'
import InteractionView from '../views/InteractionView.vue'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: Home
  },
  {
    path: '/runs/:runId/import',
    name: 'Import',
    component: ImportView,
    props: true
  },
  {
    path: '/runs/:runId/graph',
    name: 'Graph',
    component: GraphView,
    props: true
  },
  {
    path: '/runs/:runId/themes',
    name: 'Themes',
    component: ThemesView,
    props: true
  },
  {
    path: '/runs/:runId/validation',
    name: 'Validation',
    component: ValidationView,
    props: true
  },
  {
    path: '/runs/:runId/report',
    name: 'Report',
    component: ReportView,
    props: true
  },
  {
    path: '/runs/:runId/interaction',
    name: 'Interaction',
    component: InteractionView,
    props: true
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
