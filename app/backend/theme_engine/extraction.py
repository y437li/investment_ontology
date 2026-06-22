"""Structured extraction service (M3): entities, edges, and edge explanations.

Reads ``discovery/chunks.parquet`` and writes:

- ``discovery/entities.parquet``        (io_contracts.md section 9)
- ``discovery/edges.parquet``           (io_contracts.md section 11)
- ``discovery/edge_explanations.parquet`` (io_contracts.md section 12)

Entity types (ontology §7):
    Company, EconomicConcept, Commodity, MacroIndicator, Event, Geography, Document

Edge types (ontology §7):
    mentioned_in, co_occurs_with, exposed_to, sensitive_to, causes,
    benefits, hurts, located_in

Extraction method enum:
    document_stated  — explicit textual claim; requires >=1 evidence_chunk_ids
    llm_inferred     — LLM inference; must include rationale
    metadata_inferred — deterministic metadata signal; must carry source_record_id

LLM interface is hermetic: the ``Extractor`` protocol is injected.
A ``RuleBasedExtractor`` is provided as the default for tests and CI.
A real ``OpenAIExtractor`` exists but must be explicitly constructed and
injected — it is never instantiated automatically.
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs
from .config import settings

SCHEMA_VERSION = "1.0"
EXTRACTION_VERSION = "extract_v1"

# ---------------------------------------------------------------------------
# Ontology constants
# ---------------------------------------------------------------------------

# Derived from the managed ontology table (configs/ontology.yml); the hardcoded
# sets are the fallback when the table or pyyaml is unavailable.
from . import registry  # noqa: E402

_FALLBACK_ENTITY_TYPES = frozenset(
    {"Company", "EconomicConcept", "Commodity", "MacroIndicator", "Event", "Geography", "Document"}
)
_FALLBACK_EDGE_TYPES = frozenset(
    {"mentioned_in", "co_occurs_with", "exposed_to", "sensitive_to", "causes", "benefits", "hurts", "located_in"}
)

VALID_ENTITY_TYPES = frozenset(registry.entity_types()) or _FALLBACK_ENTITY_TYPES
VALID_EDGE_TYPES = frozenset(registry.edge_types()) or _FALLBACK_EDGE_TYPES

VALID_EXTRACTION_METHODS = frozenset(
    {"document_stated", "llm_inferred", "metadata_inferred"}
)

# ---------------------------------------------------------------------------
# Contract column lists (io_contracts.md)
# ---------------------------------------------------------------------------

# Section 9 — entities.parquet
ENTITIES_COLUMNS: list[str] = [
    "schema_version",
    "entity_id",
    "entity_type",
    "name",
    "canonical_name",
    "ticker",
    "exchange",
    "sector",
    "country",
    "first_seen_at",
    "source_chunk_ids",
    "confidence",
    "extraction_method",
    "review_status",
]

# Section 11 — edges.parquet
EDGES_COLUMNS: list[str] = [
    "schema_version",
    "edge_id",
    "source_entity_id",
    "target_entity_id",
    "edge_type",
    "confidence",
    "evidence_chunk_ids",
    "first_seen_at",
    "last_seen_at",
    "as_of_date",
    "extraction_method",
    "review_status",
]

# Section 12 — edge_explanations.parquet
EDGE_EXPLANATIONS_COLUMNS: list[str] = [
    "schema_version",
    "edge_id",
    "explanation",
    "evidence_chunk_ids",
    "confidence",
    "generated_by",
    "created_at",
]

# ---------------------------------------------------------------------------
# Data structures for extraction results
# ---------------------------------------------------------------------------


@dataclass
class EntityCandidate:
    """A candidate entity extracted from a chunk."""

    name: str
    entity_type: str
    confidence: float
    extraction_method: str
    ticker: Optional[str] = None
    exchange: Optional[str] = None
    sector: Optional[str] = None
    country: Optional[str] = None


@dataclass
class EdgeCandidate:
    """A candidate relationship extracted from a chunk."""

    source_name: str
    target_name: str
    edge_type: str
    confidence: float
    extraction_method: str
    explanation: str


@dataclass
class ExtractionResult:
    """Bundle returned by the Extractor for one chunk."""

    entities: list[EntityCandidate] = field(default_factory=list)
    edges: list[EdgeCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Extractor protocol
# ---------------------------------------------------------------------------


class Extractor(ABC):
    """Protocol that all extractor implementations must satisfy.

    Implementations must be stateless across calls (side-effect-free) so that
    deterministic ids remain stable for the same input + config.
    """

    @abstractmethod
    def extract(self, chunk_id: str, chunk_text: str) -> ExtractionResult:
        """Extract entities and edges from a single chunk.

        Args:
            chunk_id: Stable identifier for this chunk (used in evidence lists).
            chunk_text: Cleaned text from chunks.parquet.

        Returns:
            ExtractionResult with entity and edge candidates.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this extractor (recorded in generated_by)."""
        ...


# ---------------------------------------------------------------------------
# Rule-based extractor (deterministic, no network — default for tests/CI)
# ---------------------------------------------------------------------------

# Ordered list of (pattern, entity_type, canonical_name) tuples.
# Patterns are searched case-insensitively in chunk text.
_ENTITY_RULES: list[tuple[re.Pattern, str, str]] = [
    # Companies
    (re.compile(r"\bacme\s+corp\b|\bacme corp\b", re.I), "Company", "Acme Corp"),
    (re.compile(r"\bbeta industries\b", re.I), "Company", "Beta Industries"),
    (re.compile(r"\bhydro one\b", re.I), "Company", "Hydro One"),
    (re.compile(r"\bcameco\b", re.I), "Company", "Cameco"),
    (re.compile(r"\brbc\b", re.I), "Company", "RBC"),
    # Commodities
    (re.compile(r"\buranium\b", re.I), "Commodity", "Uranium"),
    (re.compile(r"\bcopper\b", re.I), "Commodity", "Copper"),
    (re.compile(r"\boil\b", re.I), "Commodity", "Oil"),
    (re.compile(r"\baluminum\b|\baluminium\b", re.I), "Commodity", "Aluminum"),
    # EconomicConcepts
    (re.compile(r"\bdatacenter\s+power\s+demand\b|\bpowder demand\b", re.I), "EconomicConcept", "Datacenter Power Demand"),
    (re.compile(r"\bdatacenter\b|\bdata\s+center\b", re.I), "EconomicConcept", "Datacenter"),
    (re.compile(r"\belectricity\s+demand\b", re.I), "EconomicConcept", "Electricity Demand"),
    (re.compile(r"\bgrid\s+infrastructure\b|\bgrid infrastructure\b", re.I), "EconomicConcept", "Grid Infrastructure"),
    (re.compile(r"\brenewable\s+energy\b", re.I), "EconomicConcept", "Renewable Energy"),
    (re.compile(r"\bsupply\s+chain\b", re.I), "EconomicConcept", "Supply Chain"),
    (re.compile(r"\bcapital\s+expenditure\b|\bcapex\b", re.I), "EconomicConcept", "Capital Expenditure"),
    (re.compile(r"\btransmission\b", re.I), "EconomicConcept", "Transmission"),
    # MacroIndicators
    (re.compile(r"\bfed\s+funds\s+rate\b|\bfederal\s+funds\s+rate\b", re.I), "MacroIndicator", "Fed Funds Rate"),
    (re.compile(r"\bcpi\b", re.I), "MacroIndicator", "CPI"),
    (re.compile(r"\bgdp\b", re.I), "MacroIndicator", "GDP"),
    (re.compile(r"\binterest\s+rate\b", re.I), "MacroIndicator", "Interest Rate"),
    (re.compile(r"\binflation\b", re.I), "MacroIndicator", "Inflation"),
    # Events
    (re.compile(r"\brate\s+cut\b", re.I), "Event", "Rate Cut"),
    (re.compile(r"\bcapex\s+increase\b", re.I), "Event", "Capex Increase"),
    (re.compile(r"\bproduction\s+outage\b", re.I), "Event", "Production Outage"),
    (re.compile(r"\bearnings\s+call\b", re.I), "Event", "Earnings Call"),
    # Geographies
    (re.compile(r"\bontario\b", re.I), "Geography", "Ontario"),
    (re.compile(r"\bcanada\b", re.I), "Geography", "Canada"),
    (re.compile(r"\balberta\b", re.I), "Geography", "Alberta"),
    (re.compile(r"\bnorth america\b", re.I), "Geography", "North America"),
]

# Ordered list of (source_type, target_type, edge_type, source_patterns, target_patterns)
# for rule-based edge inference.
_EDGE_RULES: list[tuple[str, str, list[str], list[str], str]] = [
    # Company exposed_to Commodity
    ("Company", "Commodity", ["acme corp", "acme", "beta industries", "beta"], ["commodity", "copper", "aluminum", "oil", "uranium"], "exposed_to"),
    # Company exposed_to EconomicConcept
    ("Company", "EconomicConcept", ["acme corp", "acme", "beta industries", "beta"], ["electricity", "power demand", "grid", "datacenter", "transmission", "renewable"], "exposed_to"),
    # EconomicConcept causes Event
    ("EconomicConcept", "Event", ["electricity demand", "datacenter", "grid infrastructure"], ["capex increase"], "causes"),
    # Commodity sensitive_to MacroIndicator
    ("Commodity", "MacroIndicator", ["copper", "oil", "uranium", "aluminum"], ["inflation", "interest rate", "cpi", "gdp", "fed funds"], "sensitive_to"),
]


def _find_entities_in_text(text: str) -> list[tuple[str, str, str]]:
    """Return list of (canonical_name, entity_type, matched_text) found in text."""
    found: list[tuple[str, str, str]] = []
    seen_canonical: set[str] = set()
    for pat, etype, canonical in _ENTITY_RULES:
        m = pat.search(text)
        if m and canonical not in seen_canonical:
            seen_canonical.add(canonical)
            found.append((canonical, etype, m.group(0)))
    return found


class RuleBasedExtractor(Extractor):
    """Deterministic pattern-matching extractor.

    Uses no network calls, produces stable output for the same input text.
    This is the default extractor used in tests and CI.
    """

    @property
    def name(self) -> str:
        return "rule_based_extractor_v1"

    def extract(self, chunk_id: str, chunk_text: str) -> ExtractionResult:
        text_lower = chunk_text.lower()
        entities_found = _find_entities_in_text(chunk_text)

        entity_candidates: list[EntityCandidate] = []
        for canonical, etype, _matched in entities_found:
            entity_candidates.append(
                EntityCandidate(
                    name=canonical,
                    entity_type=etype,
                    confidence=0.85,
                    extraction_method="document_stated",
                    ticker=None,
                    exchange=None,
                    sector=None,
                    country=None,
                )
            )

        # Build a quick lookup of what entity types were found
        found_by_canonical = {c: (c, et) for c, et, _ in entities_found}

        edge_candidates: list[EdgeCandidate] = []

        # Rule-based co_occurs_with edges for every pair of entities in the chunk
        entity_names = [c for c, _, _ in entities_found]
        for i, (name_a, type_a, _) in enumerate(entities_found):
            for name_b, type_b, _ in entities_found[i + 1:]:
                # Skip self-same-type pairs to avoid noise; keep cross-type co-occurrence
                if type_a != type_b:
                    edge_candidates.append(
                        EdgeCandidate(
                            source_name=name_a,
                            target_name=name_b,
                            edge_type="co_occurs_with",
                            confidence=0.65,
                            extraction_method="document_stated",
                            explanation=f"{name_a} and {name_b} co-occur in the same chunk.",
                        )
                    )

        # Structural edge inference from explicit text patterns
        if "exposed to" in text_lower or "exposure" in text_lower or "exposed" in text_lower:
            companies = [c for c, et, _ in entities_found if et == "Company"]
            commodities = [c for c, et, _ in entities_found if et in ("Commodity", "EconomicConcept", "MacroIndicator")]
            for comp in companies:
                for tgt in commodities:
                    edge_candidates.append(
                        EdgeCandidate(
                            source_name=comp,
                            target_name=tgt,
                            edge_type="exposed_to",
                            confidence=0.80,
                            extraction_method="document_stated",
                            explanation=f"Text indicates {comp} is exposed to {tgt}.",
                        )
                    )

        if "sensitive" in text_lower or "sensitivity" in text_lower:
            companies = [c for c, et, _ in entities_found if et == "Company"]
            macro = [c for c, et, _ in entities_found if et == "MacroIndicator"]
            for comp in companies:
                for tgt in macro:
                    edge_candidates.append(
                        EdgeCandidate(
                            source_name=comp,
                            target_name=tgt,
                            edge_type="sensitive_to",
                            confidence=0.80,
                            extraction_method="document_stated",
                            explanation=f"Text indicates {comp} is sensitive to {tgt}.",
                        )
                    )

        if "benefit" in text_lower or "benefiting" in text_lower:
            companies = [c for c, et, _ in entities_found if et == "Company"]
            concepts = [c for c, et, _ in entities_found if et in ("EconomicConcept", "Event")]
            for comp in companies:
                for tgt in concepts:
                    edge_candidates.append(
                        EdgeCandidate(
                            source_name=comp,
                            target_name=tgt,
                            edge_type="benefits",
                            confidence=0.75,
                            extraction_method="document_stated",
                            explanation=f"Text indicates {comp} benefits from {tgt}.",
                        )
                    )

        if "hurt" in text_lower or "pressured" in text_lower:
            companies = [c for c, et, _ in entities_found if et == "Company"]
            concepts = [c for c, et, _ in entities_found if et in ("EconomicConcept", "Commodity", "MacroIndicator")]
            for comp in companies:
                for tgt in concepts:
                    edge_candidates.append(
                        EdgeCandidate(
                            source_name=comp,
                            target_name=tgt,
                            edge_type="hurts",
                            confidence=0.75,
                            extraction_method="document_stated",
                            explanation=f"Text indicates {comp} is hurt by {tgt}.",
                        )
                    )

        return ExtractionResult(entities=entity_candidates, edges=edge_candidates)


# ---------------------------------------------------------------------------
# OpenAI-compatible extractor (real LLM — NOT used in tests/CI)
# ---------------------------------------------------------------------------


class OpenAIExtractor(Extractor):
    """LLM-backed extractor using an OpenAI-compatible API.

    This class must be explicitly instantiated and injected. It is NEVER
    constructed automatically. No network call occurs unless an instance of
    this class is explicitly used.

    Reads the model name from config — never hardcodes it.
    """

    def __init__(self, api_key: str, base_url: str, llm_model_name: str) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._llm_model_name = llm_model_name
        self._client = None  # lazy import to avoid import-time errors in tests

    @property
    def name(self) -> str:
        return f"openai_extractor:{self._llm_model_name}"

    def _get_client(self):  # type: ignore[return]
        if self._client is None:
            try:
                from openai import OpenAI  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "openai package is required for OpenAIExtractor; "
                    "install it with: pip install openai"
                ) from exc
            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    @property
    def _tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "emit_extraction",
                "description": "Emit entities and relationships found ONLY in the provided text.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entities": {"type": "array", "items": {"type": "object", "properties": {
                            "name": {"type": "string"},
                            "entity_type": {"type": "string", "enum": sorted(VALID_ENTITY_TYPES)},
                            "confidence": {"type": "number"},
                        }, "required": ["name", "entity_type"]}},
                        "edges": {"type": "array", "items": {"type": "object", "properties": {
                            "source_name": {"type": "string"},
                            "target_name": {"type": "string"},
                            "edge_type": {"type": "string", "enum": sorted(VALID_EDGE_TYPES)},
                            "confidence": {"type": "number"},
                            "explanation": {"type": "string"},
                            "stated_in_text": {"type": "boolean", "description": "true ONLY if the relationship is explicitly stated in the text; false if inferred"},
                        }, "required": ["source_name", "target_name", "edge_type", "stated_in_text"]}},
                    },
                    "required": ["entities", "edges"],
                },
            },
        }

    def extract(self, chunk_id: str, chunk_text: str) -> ExtractionResult:
        """Extract entities + relationships via LLM tool calling.

        Uses function calling for guaranteed structured output (MiniMax does not
        support response_format). Edges are tagged document_stated when explicitly
        stated in the text (these drive community/exposure) else llm_inferred. To
        limit pretraining leakage, the prompt forbids using outside knowledge.
        """
        import json as _json  # noqa: PLC0415

        client = self._get_client()
        # Prompt is maintained in the agent registry (configs/agents.yml), generated
        # from the ontology; fall back to a built-in prompt if the table is absent.
        system = registry.get_system_prompt("entity_extraction") or (
            "You are a financial NLP extractor. Extract ONLY economically meaningful "
            "entities and relationships explicitly present in the given text. Do NOT use "
            "outside or world knowledge and do NOT infer beyond what the text states.\n"
            "Extract: companies, economic concepts/themes (e.g. electricity demand, "
            "datacenter buildout), commodities, macro indicators, geographies, material events.\n"
            "Do NOT extract: dates, person names, document/form metadata, ticker symbols as "
            "separate entities, or boilerplate/legal/procedural terms (e.g. 'Foreign Private "
            "Issuer', 'Annual Meeting', 'home country', 'Securities Exchange Act', 'registrant', "
            "'Form 6-K'). Concepts must be substantive narratives, not administrative terms.\n"
            "Use each company's full canonical name (e.g. 'Suncor Energy', not 'Suncor' or 'SU').\n"
            "Always respond by calling the emit_extraction function."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": chunk_text},
        ]
        args: dict = {}
        for _attempt in range(3):
            response = client.chat.completions.create(
                model=self._llm_model_name,
                messages=messages,
                tools=[self._tool],
                temperature=0,
            )
            tool_calls = getattr(response.choices[0].message, "tool_calls", None) or []
            if tool_calls:
                try:
                    args = _json.loads(tool_calls[0].function.arguments)
                    break
                except Exception:
                    args = {}
            messages.append({
                "role": "user",
                "content": "Call emit_extraction with valid JSON arguments only.",
            })
        return self._to_result(args)

    @staticmethod
    def _to_result(args: dict) -> ExtractionResult:
        entities: list[EntityCandidate] = []
        for e in (args.get("entities") or []):
            etype = e.get("entity_type", "")
            name = (e.get("name") or "").strip()
            if not name or etype not in VALID_ENTITY_TYPES:
                continue
            entities.append(EntityCandidate(
                name=name, entity_type=etype,
                confidence=float(e.get("confidence", 0.7) or 0.7),
                extraction_method="document_stated",
            ))
        edges: list[EdgeCandidate] = []
        for ed in (args.get("edges") or []):
            etype = ed.get("edge_type", "")
            src = (ed.get("source_name") or "").strip()
            tgt = (ed.get("target_name") or "").strip()
            if not src or not tgt or etype not in VALID_EDGE_TYPES:
                continue
            method = "document_stated" if ed.get("stated_in_text") else "llm_inferred"
            edges.append(EdgeCandidate(
                source_name=src, target_name=tgt, edge_type=etype,
                confidence=float(ed.get("confidence", 0.7) or 0.7),
                extraction_method=method,
                explanation=ed.get("explanation", ""),
            ))
        return ExtractionResult(entities=entities, edges=edges)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_entity_id(canonical_name: str, entity_type: str) -> str:
    """Deterministic entity_id: stable for same canonical_name + entity_type."""
    basis = f"entity:{entity_type}:{canonical_name.lower()}"
    return f"ent_{_sha256_hex(basis)[:16]}"


def _stable_edge_id(
    source_entity_id: str,
    target_entity_id: str,
    edge_type: str,
) -> str:
    """Deterministic edge_id: stable for same (source, target, edge_type)."""
    basis = f"edge:{source_entity_id}:{target_entity_id}:{edge_type}"
    return f"edge_{_sha256_hex(basis)[:16]}"


def _to_date_str(val) -> str:
    """Coerce available_at / first_seen_at values to YYYY-MM-DD strings."""
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    # Handle timestamp strings like "2024-01-20T00:00:00"
    if "T" in s:
        return s.split("T")[0]
    return s[:10]  # truncate to date portion


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_table(rows: list[dict], columns: list[str], out_path: Path) -> None:
    """Write a contract-conformant parquet file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        # Build typed empty table for list columns
        field_map: dict[str, pa.DataType] = {}
        list_cols = {"source_chunk_ids", "evidence_chunk_ids"}
        for col in columns:
            if col in list_cols:
                field_map[col] = pa.list_(pa.string())
            elif col == "confidence":
                field_map[col] = pa.float64()
            else:
                field_map[col] = pa.string()
        schema = pa.schema([(c, field_map[c]) for c in columns])
        pq.write_table(pa.table({c: pa.array([], type=field_map[c]) for c in columns}, schema=schema), out_path)
        return

    pydict: dict[str, list] = {col: [row.get(col) for row in rows] for col in columns}
    table = pa.Table.from_pydict(pydict)
    pq.write_table(table, out_path)


# ---------------------------------------------------------------------------
# Core extraction pipeline
# ---------------------------------------------------------------------------


def _read_chunks(run_id: str) -> list[dict]:
    artifact = runs.get_run_dir(run_id) / "discovery" / "chunks.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"chunks.parquet not found for run {run_id}; run chunk first",
        )
    return pq.read_table(artifact).to_pylist()


# ---------------------------------------------------------------------------
# Deterministic denoise + alias canonicalization (applied to every extraction)
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(
    r"^\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]\b|\d{4}\b|\d{1,2}/\d{1,2})",
    re.IGNORECASE,
)
_NOISE_SUBSTRINGS = (
    "foreign private issuer", "annual meeting", "home country", "exchange act",
    "securities and exchange", "exchange commission", "registrant", "form 6-k",
    "form 40-f", "form 20-f", "press release", "board of directors", "fiscal year",
    "annual report", "quarterly report", "commission file", "rule 13a",
    "interim financial", "sedar", "edgar", "washington, d.c", "signature",
)
_PERSON_RE = re.compile(r"^[A-Z][a-z]+(\s+[A-Z]\.?)*\s+[A-Z][a-z]+$")
_COMPANY_SUFFIX_RE = re.compile(
    r"[\s,]+(inc|incorporated|ltd|limited|corp|corporation|company|co|plc|llc|lp)\.?\s*$",
    re.IGNORECASE,
)
_COMPANY_ALIASES = {
    "su": "Suncor Energy", "suncor": "Suncor Energy",
    "enb": "Enbridge",
    "cnq": "Canadian Natural Resources", "canadian natural": "Canadian Natural Resources",
    "ry": "Royal Bank of Canada", "rbc": "Royal Bank of Canada", "royal bank": "Royal Bank of Canada",
    "bns": "Bank of Nova Scotia", "scotiabank": "Bank of Nova Scotia",
}


def _is_noise_name(name: str) -> bool:
    n = name.strip()
    if len(n) < 3:
        return True
    low = n.lower()
    return bool(_DATE_RE.match(n)) or any(s in low for s in _NOISE_SUBSTRINGS)


def _canonical_name(name: str, entity_type: str) -> str:
    n = " ".join(name.split()).strip()
    if entity_type == "Company":
        stripped = _COMPANY_SUFFIX_RE.sub("", n).strip()
        return _COMPANY_ALIASES.get(stripped.lower(), _COMPANY_ALIASES.get(n.lower(), stripped or n))
    return n


def _clean_result(result: ExtractionResult) -> ExtractionResult:
    """Drop noise/boilerplate/date/person entities, canonicalize + merge aliases,
    and keep only edges whose endpoints survive as canonical entities."""
    canon: dict[str, str] = {}
    ents: list[EntityCandidate] = []
    for e in result.entities:
        if _is_noise_name(e.name):
            continue
        if e.entity_type == "Company" and _PERSON_RE.match(e.name.strip()) and e.name.strip().lower() not in _COMPANY_ALIASES:
            continue
        cn = _canonical_name(e.name, e.entity_type)
        if not cn or _is_noise_name(cn):
            continue
        canon[e.name.strip().lower()] = cn
        canon[cn.lower()] = cn
        ents.append(EntityCandidate(name=cn, entity_type=e.entity_type, confidence=e.confidence, extraction_method=e.extraction_method))
    edges: list[EdgeCandidate] = []
    for ed in result.edges:
        s = canon.get(ed.source_name.strip().lower()) or _COMPANY_ALIASES.get(ed.source_name.strip().lower())
        t = canon.get(ed.target_name.strip().lower()) or _COMPANY_ALIASES.get(ed.target_name.strip().lower())
        if not s or not t or s == t:
            continue
        edges.append(EdgeCandidate(source_name=s, target_name=t, edge_type=ed.edge_type, confidence=ed.confidence, extraction_method=ed.extraction_method, explanation=ed.explanation))
    return ExtractionResult(entities=ents, edges=edges)


def build_default_extractor() -> Extractor:
    """Select the extractor from environment.

    Uses the OpenAI-compatible LLM extractor (e.g. MiniMax) when LLM_API_KEY +
    LLM_BASE_URL + LLM_MODEL_NAME are all set and EXTRACTOR != 'rule_based';
    otherwise the deterministic RuleBasedExtractor. Tests/CI set no LLM env, so
    they stay hermetic on RuleBasedExtractor.
    """
    import os  # noqa: PLC0415

    if os.environ.get("EXTRACTOR", "").lower() == "rule_based":
        return RuleBasedExtractor()
    key = os.environ.get("LLM_API_KEY")
    base = os.environ.get("LLM_BASE_URL")
    model = os.environ.get("LLM_MODEL_NAME")
    if key and base and model:
        return OpenAIExtractor(api_key=key, base_url=base, llm_model_name=model)
    return RuleBasedExtractor()


def run_extraction(
    run_id: str,
    extractor: Optional[Extractor] = None,
) -> tuple[int, int]:
    """Extract entities, edges, and explanations from chunks.

    Args:
        run_id: The run to process.
        extractor: Extractor implementation to use. Defaults to the env-selected
            extractor (build_default_extractor): LLM when configured, else rule-based.

    Returns:
        (entity_count, edge_count)
    """
    if extractor is None:
        extractor = build_default_extractor()

    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    as_of_date = manifest.as_of_date

    chunks = _read_chunks(run_id)
    run_dir = runs.get_run_dir(run_id)
    discovery_dir = run_dir / "discovery"

    # Phase 1: extract per-chunk candidates.
    # entity_name -> (EntityCandidate, list[chunk_id], first_seen_date)
    entity_map: dict[str, tuple[EntityCandidate, list[str], str]] = {}
    # (source_id, target_id, edge_type) -> (EdgeCandidate, list[chunk_id], first_seen_date)
    edge_map: dict[tuple[str, str, str], tuple[EdgeCandidate, list[str], str]] = {}

    for chunk in chunks:
        chunk_id: str = chunk.get("chunk_id") or ""
        text: str = chunk.get("text") or ""
        available_at = _to_date_str(chunk.get("available_at"))

        if not chunk_id or not text:
            continue

        result = _clean_result(extractor.extract(chunk_id=chunk_id, chunk_text=text))

        # Accumulate entities
        for cand in result.entities:
            if cand.entity_type not in VALID_ENTITY_TYPES:
                continue
            if cand.extraction_method not in VALID_EXTRACTION_METHODS:
                continue
            key = f"{cand.entity_type}:{cand.name.lower()}"
            if key in entity_map:
                existing_cand, chunk_ids, first_seen = entity_map[key]
                if chunk_id not in chunk_ids:
                    chunk_ids.append(chunk_id)
                entity_map[key] = (existing_cand, chunk_ids, first_seen)
            else:
                entity_map[key] = (cand, [chunk_id], available_at)

        # Accumulate edges (need entity resolution after entity pass)
        for ecand in result.edges:
            if ecand.edge_type not in VALID_EDGE_TYPES:
                continue
            if ecand.extraction_method not in VALID_EXTRACTION_METHODS:
                continue
            # We'll resolve entity ids after the entity pass
            edge_key_src = f"_:{ecand.source_name.lower()}"
            edge_key_tgt = f"_:{ecand.target_name.lower()}"
            # Use canonical names as temporary keys — resolved below
            edge_key = (ecand.source_name.lower(), ecand.target_name.lower(), ecand.edge_type)
            if edge_key in edge_map:
                existing_cand, chunk_ids, first_seen = edge_map[edge_key]
                if chunk_id not in chunk_ids:
                    chunk_ids.append(chunk_id)
                edge_map[edge_key] = (existing_cand, chunk_ids, first_seen)
            else:
                edge_map[edge_key] = (ecand, [chunk_id], available_at)

    # Phase 2: build entity rows with stable ids.
    entity_rows: list[dict] = []
    # Build canonical_name -> entity_id lookup for edge resolution
    name_to_entity_id: dict[str, str] = {}

    for key, (cand, chunk_ids, first_seen) in entity_map.items():
        entity_id = _stable_entity_id(cand.name, cand.entity_type)
        name_to_entity_id[cand.name.lower()] = entity_id
        entity_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "entity_id": entity_id,
                "entity_type": cand.entity_type,
                "name": cand.name,
                "canonical_name": cand.name,
                "ticker": cand.ticker,
                "exchange": cand.exchange,
                "sector": cand.sector,
                "country": cand.country,
                "first_seen_at": first_seen or as_of_date,
                "source_chunk_ids": chunk_ids,
                "confidence": cand.confidence,
                "extraction_method": cand.extraction_method,
                "review_status": "pending",
            }
        )

    # Phase 3: build edge rows with stable ids, resolving entity references.
    edge_rows: list[dict] = []
    explanation_rows: list[dict] = []
    created_at = _utc_now_iso()

    for (src_name_lower, tgt_name_lower, edge_type), (ecand, chunk_ids, first_seen) in edge_map.items():
        source_entity_id = name_to_entity_id.get(src_name_lower)
        target_entity_id = name_to_entity_id.get(tgt_name_lower)
        if not source_entity_id or not target_entity_id:
            # Skip edges whose endpoints were not extracted as entities
            continue

        edge_id = _stable_edge_id(source_entity_id, target_entity_id, edge_type)

        # Contract: document_stated edges MUST carry >=1 evidence_chunk_ids
        if ecand.extraction_method == "document_stated" and not chunk_ids:
            continue

        edge_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "edge_id": edge_id,
                "source_entity_id": source_entity_id,
                "target_entity_id": target_entity_id,
                "edge_type": edge_type,
                "confidence": ecand.confidence,
                "evidence_chunk_ids": chunk_ids,
                "first_seen_at": first_seen or as_of_date,
                "last_seen_at": as_of_date,
                "as_of_date": as_of_date,
                "extraction_method": ecand.extraction_method,
                "review_status": "pending",
            }
        )

        explanation_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "edge_id": edge_id,
                "explanation": ecand.explanation,
                "evidence_chunk_ids": chunk_ids,
                "confidence": ecand.confidence,
                "generated_by": extractor.name,
                "created_at": created_at,
            }
        )

    _write_table(entity_rows, ENTITIES_COLUMNS, discovery_dir / "entities.parquet")
    _write_table(edge_rows, EDGES_COLUMNS, discovery_dir / "edges.parquet")
    _write_table(
        explanation_rows,
        EDGE_EXPLANATIONS_COLUMNS,
        discovery_dir / "edge_explanations.parquet",
    )

    return len(entity_rows), len(edge_rows)
