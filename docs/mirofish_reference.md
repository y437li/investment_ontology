# MiroFish Implementation Reference

Use MiroFish as a workflow and UI reference only. The project logic, artifact contracts, and naming must follow `theme_discovery_engine_v1.md` and `docs/io_contracts.md`.

## Source-of-Truth Priority

1. `theme_discovery_engine_v1.md`
2. `docs/io_contracts.md`
3. `docs/code_style_standards.md`
4. this MiroFish reference

If MiroFish conflicts with those files, do not copy MiroFish.

## Borrow

- Vue + Vite frontend shell.
- Flask backend if modifying the MiroFish codebase directly.
- File upload flow.
- Text extraction and chunking pattern.
- Project/run state pattern.
- Background task progress pattern.
- Report page and interaction page concepts.
- Thin Flask route handlers that delegate to services.
- Vue router-driven workflow pages.

## Replace

- Replace simulation pages with theme discovery pages.
- Replace OASIS/Twitter/Reddit run logic with graph/community/validation pipeline.
- Replace Zep-dependent persistence with local run artifacts for MVP.
- Replace prediction language with evidence-backed research language.
- Replace MiroFish's simulation/project nouns with this project's run/artifact/stage nouns.

## Route Mapping

```text
MiroFish Home                  -> Theme Engine Home
MiroFish Process               -> Data Import + Extraction + Graph Build
MiroFish Simulation            -> Theme Discovery
MiroFish SimulationRun         -> Exposure + Freeze + Validation
MiroFish Report                -> Evidence-backed Report
MiroFish Interaction           -> Evidence Q&A
```

## Backend Mapping

```text
/api/graph/ontology/generate   -> /api/data/import + /api/extraction/run
/api/graph/build               -> /api/graph/build
/api/simulation/create         -> /api/themes/discover
/api/simulation/prepare        -> remove or bypass
/api/simulation/start          -> /api/exposure/compute + /api/discovery/freeze + /api/validation/run
/api/report/*                  -> keep concept, restrict to artifacts
```

Key rule:

Report generation can only use artifacts produced by the run. It cannot invent validation claims.

## Implementation Notes From MiroFish

- Its frontend route flow is `Home -> Process -> Simulation -> SimulationRun -> Report -> Interaction`.
- Its Flask app registers separate blueprints for graph, simulation, and report APIs.
- Its backend is primarily file-backed, with uploaded documents and JSON state files as important truth surfaces.
- Its task manager pattern is useful for local progress updates, but v1 should not treat it as durable production job infrastructure.

Use these as engineering patterns only. The economic narrative pipeline remains:

```text
Raw Unstructured Sources -> Cleaned Documents/Chunks -> Structured Entities/Edges -> Graph(t) -> Communities -> Theme Snapshots -> Exposure -> Freeze -> Validation -> Report
```
