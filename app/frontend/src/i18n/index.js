import { createI18n } from 'vue-i18n'

const messages = {
  en: {
    nav: {
      brand: 'THEME ENGINE',
      home: 'Home',
      docs: 'Docs'
    },
    home: {
      tagline: 'NARRATIVE RESEARCH',
      version: 'v0.1.0',
      heroTitle1: 'Economic Narrative',
      heroTitle2: 'Theme Discovery',
      heroDesc: 'Evidence-backed economic narrative and theme discovery from financial filings and research documents. Build knowledge graphs, detect communities, and compute company-theme exposure scores.',
      systemStatus: 'SYSTEM STATUS',
      systemReady: 'Engine Ready',
      systemReadyDesc: 'Create a new run with an as-of date to start the discovery pipeline.',
      workflowSequence: 'PIPELINE SEQUENCE',
      step01Title: 'Data Import & Clean',
      step01Desc: 'Ingest source documents and apply data quality gates.',
      step02Title: 'Extraction & Resolution',
      step02Desc: 'Extract entities and edges; resolve entity aliases.',
      step03Title: 'Graph Build',
      step03Desc: 'Construct the structural knowledge graph.',
      step04Title: 'Theme Discovery',
      step04Desc: 'Detect communities and label emerging themes.',
      step05Title: 'Validation & Report',
      step05Desc: 'Compute exposure, validate, and generate the research report.',
      createRunLabel: 'AS-OF DATE',
      createRunPlaceholder: 'YYYY-MM-DD',
      createRunBtn: 'Create Run',
      creating: 'Creating...',
      recentRuns: 'RECENT RUNS',
      noRuns: 'No runs yet. Create your first run above.',
      runId: 'Run ID',
      asOfDate: 'As-of Date',
      created: 'Created',
      frozen: 'Frozen',
      open: 'Open'
    },
    pipeline: {
      title: 'Pipeline',
      runId: 'Run ID',
      asOfDate: 'As-of Date',
      status: 'Status',
      stepImport: 'Data Import',
      stepClean: 'Data Clean',
      stepChunk: 'Chunk',
      stepExtract: 'Extraction',
      stepResolve: 'Entity Resolution',
      stepGraph: 'Graph Build',
      stepThemes: 'Theme Discovery',
      stepExposure: 'Exposure Compute',
      stepFreeze: 'Discovery Freeze',
      stepValidation: 'Validation',
      stepReport: 'Report Generate',
      run: 'Run',
      running: 'Running...',
      done: 'Done',
      pending: 'Pending',
      failed: 'Failed',
      documentsDir: 'Documents Directory',
      manifestPath: 'Source Manifest Path',
      viewGraph: 'View Graph',
      viewThemes: 'View Themes',
      viewValidation: 'View Validation',
      viewReport: 'View Report',
      viewInteraction: 'Evidence Q&A'
    },
    graph: {
      title: 'Graph Explorer',
      panelTitle: 'Knowledge Graph',
      loading: 'Loading graph...',
      empty: 'No graph data. Run the pipeline first.',
      nodes: 'Nodes',
      edges: 'Edges',
      nodeDetails: 'Node Details',
      relationship: 'Relationship',
      refreshGraph: 'Refresh',
      toggleMaximize: 'Toggle fullscreen',
      entityTypes: 'Entity Types',
      showEdgeLabels: 'Show Edge Labels'
    },
    themes: {
      title: 'Theme Radar',
      communities: 'Communities',
      noCommunities: 'No theme data yet. Run theme discovery first.',
      themeMetrics: 'Theme Metrics',
      exposure: 'Company Exposure',
      strength: 'Strength',
      cohesion: 'Cohesion',
      saturation: 'Saturation',
      state: 'State',
      topEntities: 'Top Entities',
      topCompanies: 'Top Companies'
    },
    validation: {
      title: 'Validation',
      noData: 'No validation data. Run the validation step first.',
      communityId: 'Community',
      themeName: 'Theme',
      validationStatus: 'Status'
    },
    report: {
      title: 'Research Report',
      noData: 'No report generated yet. Complete the pipeline first.'
    },
    interaction: {
      title: 'Evidence Q&A',
      placeholder: 'Ask a question about the discovered themes and evidence...',
      send: 'Send',
      noData: 'Complete the pipeline to explore evidence.'
    },
    common: {
      back: 'Back',
      loading: 'Loading...',
      error: 'Error',
      success: 'Success',
      cancel: 'Cancel',
      close: 'Close'
    }
  }
}

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  fallbackLocale: 'en',
  messages
})

export default i18n
