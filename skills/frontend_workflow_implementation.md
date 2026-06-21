# Skill: Frontend Workflow Implementation

Purpose:

Implement the Vue workflow pages that make the pipeline inspectable.

Use when:

- Adding Data Import, Graph Build, Theme Discovery, Validation, Report, or Evidence Q&A screens.
- Wiring frontend API clients.
- Building tables, charts, and graph drilldowns.

Inputs:

- `theme_discovery_engine_v1.md`
- `docs/mirofish_reference.md`
- backend API contracts.
- sample run artifacts.

Steps:

1. Keep the workflow pages aligned to the MVP route shape.
2. Build API client functions before page-specific calls.
3. Show stage status, errors, and artifact availability.
4. Prefer dense, inspectable research UI over marketing-style screens.
5. Make evidence drilldown available from entities, edges, themes, and report claims.
6. Keep report text linked to artifacts or evidence.
7. Add a frontend build or smoke check.

Outputs:

- Vue routes and views.
- API client modules.
- reusable tables, graph panels, and validation charts.
- frontend build/test updates.

Acceptance checks:

- User can see current run id and `as_of_date`.
- UI shows which artifacts exist or are missing.
- Theme detail links to companies, edges, and evidence.
- Validation page shows benchmark comparison and caveats.
- Report page does not hide evidence provenance.
- Frontend build passes.

Failure modes:

- Building a polished landing page instead of the research workflow.
- Displaying LLM summaries without evidence links.
- Hiding pipeline errors.
- Hardcoding demo data into components.

