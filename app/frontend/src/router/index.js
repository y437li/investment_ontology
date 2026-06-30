import { createRouter, createWebHistory } from 'vue-router'
import MainThemeView from '../views/MainThemeView.vue'
import Home from '../views/Home.vue'
import AdminView from '../views/AdminView.vue'
import ImportView from '../views/ImportView.vue'
import GraphView from '../views/GraphView.vue'
import ThemesView from '../views/ThemesView.vue'
import ValidationView from '../views/ValidationView.vue'
import PanelView from '../views/PanelView.vue'
import ReportView from '../views/ReportView.vue'
import InteractionView from '../views/InteractionView.vue'
import CompanyView from '../views/CompanyView.vue'
import ScenarioView from '../views/ScenarioView.vue'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: Home
  },
  {
    path: '/admin',
    name: 'Admin',
    component: AdminView
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
    path: '/runs/:runId/main-theme',
    name: 'MainTheme',
    component: MainThemeView,
    props: true
  },
  {
    path: '/runs/:runId/validation',
    name: 'Validation',
    component: ValidationView,
    props: true
  },
  {
    // OI-6 R3b: multi-period panel (lineage, exposure trajectories, validation)
    path: '/runs/:runId/panel',
    name: 'Panel',
    component: PanelView,
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
  },
  {
    // EG-C: per-company detail page
    // company_id is the entity id (ent_...) — passed as a route param
    path: '/runs/:runId/companies/:companyId',
    name: 'Company',
    component: CompanyView,
    props: true
  },
  {
    // FI-F: projected scenarios (data-driven Event triggers -> company impacts)
    // v1: browse-only; no user scenario input (that is v1.1)
    path: '/runs/:runId/scenarios',
    name: 'Scenarios',
    component: ScenarioView,
    props: true
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
