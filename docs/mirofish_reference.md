# MiroFish Implementation Reference

Use MiroFish as a workflow and UI reference only.

## Borrow

- Vue + Vite frontend shell.
- Flask backend if modifying the MiroFish codebase directly.
- File upload flow.
- Text extraction and chunking pattern.
- Project/run state pattern.
- Background task progress pattern.
- Report page and interaction page concepts.

## Replace

- Replace simulation pages with theme discovery pages.
- Replace OASIS/Twitter/Reddit run logic with graph/community/validation pipeline.
- Replace Zep-dependent persistence with local run artifacts for MVP.
- Replace prediction language with evidence-backed research language.

## Route Mapping

```text
MiroFish Home                  -> Theme Engine Home
MiroFish Process               -> Data Import + Graph Build
MiroFish Simulation            -> Theme Discovery
MiroFish SimulationRun         -> Validation Run
MiroFish Report                -> Evidence-backed Report
MiroFish Interaction           -> Evidence Q&A
```

## Backend Mapping

```text
/api/graph/ontology/generate   -> /api/data/import + /api/extraction/run
/api/graph/build               -> /api/graph/build
/api/simulation/create         -> /api/themes/discover
/api/simulation/prepare        -> remove or bypass
/api/simulation/start          -> /api/validation/run
/api/report/*                  -> keep concept, restrict to artifacts
```

Key rule:

Report generation can only use artifacts produced by the run. It cannot invent validation claims.

