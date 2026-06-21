# Code Style Standards

This project follows a Waterloo CS136-inspired engineering style: clear contracts, explicit inputs and outputs, small testable units, no hidden state, helper decomposition, and comments that preserve reasoning.

This is not a claim of strict compliance with an official CS136 course handout. It is the project standard inspired by the CS136 style of writing code with explicit design recipes, contracts, helpers, constants, and tests.

## 1. Design Principles

### Encapsulate When It Reduces Risk

Prefer small reusable functions, classes, or services when logic is repeated, stateful, or easy to misuse.

Encapsulate:

- artifact read/write logic
- config loading and validation
- `available_at` filtering
- LLM provider calls
- schema validation
- graph construction
- community detection
- company exposure scoring
- validation windows and benchmark calculation

Do not over-abstract:

- one-line transformations
- code that is only used once and is self-explanatory
- temporary migration or fixture setup

Rule:

> If a behavior must stay consistent across runs, it should live behind a named function or service boundary.

### No Hardcoded Research Logic

Do not hardcode:

- company tickers
- dates
- model names
- paths
- thresholds
- benchmark names
- validation windows
- community algorithm choice
- edge confidence cutoffs

Use:

- `configs/*.yml`
- `.env`
- named constants
- function parameters
- run manifest fields

Acceptable hardcoding:

- enum values defined by the ontology
- artifact filenames listed in the source-of-truth spec
- small local test fixtures

## 2. Naming

Python:

- files: `lowercase_snake_case.py`
- functions: `lowercase_snake_case`
- variables: `lowercase_snake_case`
- classes: `PascalCase`
- constants: `UPPERCASE_SNAKE_CASE`

Frontend:

- Vue components: `PascalCase.vue`
- API client methods: verb-first names such as `createRun`, `fetchRunStatus`, `buildGraph`

Config keys:

- lowercase snake case

Artifacts:

- lowercase snake case columns
- explicit ids such as `document_id`, `entity_id`, `edge_id`, `community_id`

## 3. Function Contract Template

Use docstrings for non-trivial functions. Follow a CS136-inspired design recipe:

1. Purpose: what the function computes or changes.
2. Contract: input and output types.
3. Requires: preconditions that callers must satisfy.
4. Effects: filesystem, network, cache, database, or artifact writes.
5. Raises: expected exceptions.
6. Tests/examples: covered in tests or fixtures.

Python template:

```python
def build_graph_snapshot(run_id: str, as_of_date: date, config: GraphConfig) -> GraphSnapshot:
    """Build a point-in-time graph snapshot for a run.

    Args:
        run_id: Run identifier whose artifacts should be read.
        as_of_date: Maximum information date allowed in the graph.
        config: Graph construction settings.

    Requires:
        `data/runs/<run_id>/entities.parquet` and `edges.parquet` exist.

    Effects:
        Does not write artifacts; callers own persistence.

    Returns:
        GraphSnapshot containing nodes, edges, and provenance metadata.

    Raises:
        ArtifactMissingError: If required upstream artifacts are unavailable.
        LeakageError: If any input row is newer than as_of_date.
    """
```

JavaScript or TypeScript template:

```js
/**
 * Fetches artifact metadata for a run.
 *
 * Requires:
 *   runId is a non-empty run identifier.
 *
 * Effects:
 *   Performs an HTTP GET request. Does not mutate frontend state.
 *
 * @param {string} runId - Run identifier.
 * @returns {Promise<ArtifactSummary[]>} Available artifacts and status metadata.
 * @throws {ApiError} When the backend rejects the request.
 */
export async function fetchArtifactSummary(runId) {}
```

Short helper functions do not need long docstrings if names and types are clear.

## 4. Helper Decomposition

Break complex code into helpers when a block has a distinct purpose, repeated behavior, or a rule that must stay consistent.

Good helper boundaries:

- `load_pipeline_config`
- `validate_as_of_date`
- `filter_documents_as_of`
- `write_run_artifact`
- `read_required_artifact`
- `extract_entities_from_chunk`
- `build_weighted_graph`
- `compute_forward_return`

Avoid helpers with vague names:

- `process_data`
- `handle_stuff`
- `do_graph`
- `run_all`

Rule:

> If a helper cannot be described in one clear sentence, the boundary is probably wrong.

## 5. Constants and Magic Values

Use named constants or config values for any value that carries meaning.

Good:

```python
DEFAULT_CHUNK_SIZE = 800
MIN_EDGE_CONFIDENCE = 0.55
REQUIRED_DOCUMENT_COLUMNS = ["document_id", "available_at", "raw_path"]
```

Better for research parameters:

```yaml
chunk_size: 800
min_edge_confidence: 0.55
forward_windows:
  - 1M
  - 3M
```

Bad:

```python
if edge["confidence"] > 0.55:
    ...
```

Accept numeric literals only when they are structurally obvious, such as `0`, `1`, empty slices, or small loop increments.

## 6. Module Header Template

Use module headers when a file owns an important boundary.

```python
"""Artifact readers and writers for Theme Discovery Engine runs.

This module centralizes artifact paths and schema checks so pipeline stages do
not write ad hoc files outside `data/runs/<run_id>/`.
"""
```

Avoid headers that only repeat the filename.

## 7. Comments

Good comments explain:

- why the code exists
- assumptions
- invariants
- leakage-sensitive rules
- non-obvious tradeoffs
- source limitations

Bad comments:

- restate the line of code
- narrate obvious assignments
- preserve outdated behavior
- hide uncertainty

Examples:

```python
# Use available_at rather than period_end; revised filings can arrive after the reported period.
eligible_docs = documents[documents["available_at"] <= as_of_date]
```

```python
# Freeze discovery outputs before loading returns to prevent accidental feature leakage.
freeze_discovery_artifacts(run_id)
```

## 8. Configuration and Variables

Every threshold or environment-dependent value should be configurable.

Examples:

```yaml
chunk_size: 800
chunk_overlap: 120
min_edge_confidence: 0.55
community_algorithm: leiden
forward_windows:
  - 1M
  - 3M
```

Code should load these values from config objects:

```python
chunks = split_text(text, size=config.chunk_size, overlap=config.chunk_overlap)
```

Avoid:

```python
chunks = split_text(text, size=800, overlap=120)
```

unless the values are in a small test fixture.

## 9. Tests and Examples

CS136-style work expects examples/tests close to the design. In this project, tests can live in `tests/`, but every meaningful behavior should have a small example or fixture.

Minimum examples/tests by function type:

- pure helper: input/output unit test
- artifact writer: fixture run directory test
- artifact reader: missing-file and malformed-schema test
- `available_at` filter: future row exclusion test
- validation function: known forward return fixture

## 10. Service Boundaries

Recommended backend boundaries:

```text
api/                 thin HTTP routes
services/            reusable application services
pipelines/           run stages
artifacts/           artifact readers/writers/schema checks
schemas/             Pydantic models
graph/               graph construction and community logic
validation/          forward validation logic
reports/             report generation from artifacts
```

Route handlers should:

1. Validate request.
2. Call service or pipeline.
3. Return task status or artifact metadata.

Route handlers should not:

- parse PDFs directly
- call LLM providers directly
- run graph algorithms inline
- compute validation inline

## 11. Error Handling

Errors should include:

- `run_id`
- stage name
- failed artifact or config
- actionable message

Example:

```text
run_id=run_20240630_120000 stage=ingestion error=missing_available_at document=hydro_one_q2.pdf
```

Do not swallow exceptions that affect artifact correctness.

## 12. Test Expectations

Every meaningful code change should include one of:

- unit test
- fixture-based pipeline test
- API smoke test
- frontend build check

Required tests by area:

- ingestion: `available_at` filtering
- extraction: required schema fields and evidence ids
- graph: deterministic community output with seeded fixture
- validation: discovery freeze before future returns
- report: claims reference artifacts or evidence

## 13. Review Checklist

Before accepting code:

- Is repeated logic encapsulated?
- Are thresholds and paths configurable?
- Are function names precise?
- Are comments useful and current?
- Are leakage-sensitive rules documented near the code?
- Are artifacts written through a consistent boundary?
- Did tests or smoke checks run?
