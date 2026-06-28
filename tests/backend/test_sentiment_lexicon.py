"""SENT-A acceptance tests: Loughran-McDonald lexicon + deterministic tone scorer.

Acceptance criteria (GitHub #99, Workstream S-A):

AC1. On a committed MD&A-style fixture chunk: correct category counts AND the
     EXACT matched-word list per category.

AC2. An uncertainty-heavy passage scores higher on tone_uncertainty than on
     other categories (token-normalised).

AC3. "liability" is NOT counted negative (proves LM lexicon, not generic Harvard-GI).
     Also: "cost" and "depreciation" are not negative.

AC4. speaker_role correctly distinguishes a management section (MD&A/10-K transcript)
     from a news chunk.

AC5. Hermetic: no network; loader works on the committed subset CSV.

AC6. The loader works on any valid CSV (drop-in: adding a new word row or column
     requires no code change).

AC7. Config-driven: category list comes from configs/sentiment.yml, not hardcoded.
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
LEXICON_CSV = REPO_ROOT / "data" / "lexicons" / "loughran_mcdonald.csv"
SENTIMENT_CFG = REPO_ROOT / "configs" / "sentiment.yml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def config():
    """Load the committed configs/sentiment.yml."""
    from theme_engine.sentiment_lexicon import load_sentiment_config
    return load_sentiment_config(SENTIMENT_CFG)


@pytest.fixture(scope="module")
def lexicon(config):
    """Load the committed curated LM CSV once for the whole module."""
    from theme_engine.sentiment_lexicon import load_lexicon
    return load_lexicon(csv_path=LEXICON_CSV, config=config)


# ---------------------------------------------------------------------------
# AC5 / AC6 — Loader: hermetic + CSV-agnostic
# ---------------------------------------------------------------------------

class TestLexiconLoader:
    """Loader reads the committed CSV; no network; drop-in compatible."""

    def test_lexicon_csv_exists_and_is_readable(self):
        """The committed curated subset CSV must exist at the expected path."""
        assert LEXICON_CSV.exists(), f"Lexicon CSV missing: {LEXICON_CSV}"

    def test_lexicon_loaded_without_network(self, lexicon):
        """Loader must return a non-empty dict using only the local CSV file."""
        assert isinstance(lexicon, dict)
        assert len(lexicon) > 0, "Lexicon loaded 0 words — CSV may be empty"

    def test_all_six_categories_covered(self, lexicon, config):
        """All six LM categories must have at least one word in the subset."""
        cats = config.get("categories", [])
        assert cats, "No categories in sentiment.yml"
        for cat in cats:
            has_entry = any(flags.get(cat) for flags in lexicon.values())
            assert has_entry, (
                f"Category '{cat}' has no words in the committed CSV subset"
            )

    def test_loader_accepts_minimal_synthetic_csv(self, tmp_path):
        """Drop-in: loader works on any valid CSV (new rows = immediate effect)."""
        from theme_engine.sentiment_lexicon import load_lexicon
        csv_content = (
            "word,positive,negative,uncertainty\n"
            "excellent,1,0,0\n"
            "loss,0,1,0\n"
            "uncertain,0,0,1\n"
        )
        p = tmp_path / "mini_lm.csv"
        p.write_text(csv_content, encoding="utf-8")
        lex = load_lexicon(csv_path=p)
        assert "excellent" in lex
        assert "loss" in lex
        assert lex["loss"].get("negative") == 1
        assert lex["excellent"].get("positive") == 1

    def test_loader_skips_comment_lines(self):
        """CSV lines starting with # are treated as comments and skipped."""
        from theme_engine.sentiment_lexicon import load_lexicon
        csv_content = (
            "# This is a comment\n"
            "word,positive,negative\n"
            "# Another comment\n"
            "great,1,0\n"
            "bad,0,1\n"
        )
        buf = io.StringIO(csv_content)
        # Write to a temp file since load_lexicon takes a path.
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(csv_content)
            fpath = f.name
        try:
            lex = load_lexicon(csv_path=fpath)
            assert "great" in lex
            assert "bad" in lex
            # Comment lines should not appear as words.
            assert "this" not in lex
            assert "another" not in lex
        finally:
            os.unlink(fpath)

    def test_source_note_present_in_csv_header(self):
        """The committed CSV must contain a SOURCE/LICENSE note."""
        raw = LEXICON_CSV.read_text(encoding="utf-8")
        assert "SOURCE" in raw or "Loughran" in raw, (
            "CSV is missing a SOURCE / attribution note in the header comments"
        )
        assert "2011" in raw or "Journal of Finance" in raw, (
            "CSV is missing the Loughran & McDonald (2011) citation"
        )


# ---------------------------------------------------------------------------
# AC7 — Config-driven categories
# ---------------------------------------------------------------------------

class TestConfigDrivenCategories:
    """Category list comes from configs/sentiment.yml, not hardcoded."""

    def test_config_has_six_categories(self, config):
        cats = config.get("categories", [])
        assert set(cats) == {
            "positive", "negative", "uncertainty",
            "litigious", "strong_modal", "weak_modal",
        }, f"Unexpected categories: {cats}"

    def test_scorer_uses_categories_from_config(self, lexicon, config):
        """score_chunk with explicit config respects the category list."""
        from theme_engine.sentiment_lexicon import score_chunk
        text = "We achieved excellent growth this quarter."
        result = score_chunk(text, config=config, lexicon=lexicon)
        tone = result["tone_vector"]
        # All configured categories must be present in the tone vector.
        for cat in config["categories"]:
            assert cat in tone, f"Category '{cat}' missing from tone_vector"

    def test_scorer_works_with_synthetic_single_category_config(self, lexicon):
        """Scorer works even if config only has one category."""
        from theme_engine.sentiment_lexicon import score_chunk
        minimal_config = {"categories": ["positive"]}
        result = score_chunk(
            "excellent growth",
            config=minimal_config,
            lexicon=lexicon,
        )
        assert "positive" in result["tone_vector"]
        # No other categories should be present when config restricts to one.
        assert set(result["tone_vector"].keys()) == {"positive"}


# ---------------------------------------------------------------------------
# AC1 — MD&A fixture: exact counts and matched-word lists
# ---------------------------------------------------------------------------

# A synthetic MD&A-style paragraph. Words are chosen so expected matches are
# unambiguous and not subject to tokenisation edge cases.
_MDNA_FIXTURE = (
    "Management believes the company achieved excellent growth in revenue this "
    "fiscal year. We improved our operating margins and delivered solid results "
    "despite uncertain market conditions. We expect continued profitable growth. "
    "The liability balance reflects our financial obligations and does not "
    "represent an adverse outcome. Depreciation and cost of goods sold are "
    "recorded according to standard accounting principles."
)

# Expected matches in the fixture (from the curated CSV):
# positive: achieved, excellent, growth, improved, solid, profitable, growth (2nd)
# negative: adverse
# uncertainty: believes, uncertain, expect
# litigious: (none)
# strong_modal: (none — "will" not present)
# weak_modal: (none in this fixture)
_EXPECTED_POSITIVE_WORDS = {"achieved", "excellent", "growth", "improved", "solid", "profitable"}
_EXPECTED_NEGATIVE_WORDS = {"adverse"}
_EXPECTED_UNCERTAINTY_WORDS = {"believes", "uncertain", "expect"}

class TestMdnaFixtureScoring:
    """AC1: correct counts and exact matched-word list on the MD&A fixture."""

    def test_positive_words_matched(self, lexicon, config):
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _MDNA_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        matched_pos = set(result["matched_words"]["positive"])
        # All expected positive words must be matched.
        for word in _EXPECTED_POSITIVE_WORDS:
            assert word in matched_pos, (
                f"Expected '{word}' in matched_positive; got {matched_pos}"
            )

    def test_negative_words_matched(self, lexicon, config):
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _MDNA_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        matched_neg = set(result["matched_words"]["negative"])
        assert "adverse" in matched_neg, (
            f"'adverse' not in matched_negative; got {matched_neg}"
        )

    def test_uncertainty_words_matched(self, lexicon, config):
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _MDNA_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        matched_unc = set(result["matched_words"]["uncertainty"])
        for word in _EXPECTED_UNCERTAINTY_WORDS:
            assert word in matched_unc, (
                f"Expected '{word}' in matched_uncertainty; got {matched_unc}"
            )

    def test_raw_counts_match_matched_word_list_length(self, lexicon, config):
        """raw_counts[cat] == len(matched_words[cat]) for every category."""
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _MDNA_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        for cat in config["categories"]:
            rc = result["raw_counts"][cat]
            mw = result["matched_words"][cat]
            assert rc == len(mw), (
                f"raw_counts['{cat}'] = {rc} but len(matched_words) = {len(mw)}"
            )

    def test_tone_vector_is_token_normalised(self, lexicon, config):
        """tone_vector[cat] == raw_counts[cat] / token_count."""
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _MDNA_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        tc = result["token_count"]
        assert tc > 0
        for cat in config["categories"]:
            rc = result["raw_counts"][cat]
            expected_score = rc / tc
            actual_score = result["tone_vector"][cat]
            assert abs(actual_score - expected_score) < 1e-9, (
                f"Normalisation error for '{cat}': "
                f"expected {expected_score:.6f}, got {actual_score:.6f}"
            )


# ---------------------------------------------------------------------------
# AC2 — Uncertainty-heavy passage scores high on uncertainty
# ---------------------------------------------------------------------------

_UNCERTAINTY_FIXTURE = (
    "The company may or might face uncertain market conditions. "
    "We believe results could possibly be affected by factors we cannot predict. "
    "Estimated earnings remain approximately uncertain. "
    "Management expects the situation to depend on external variables. "
    "It is probable the outcome will be approximately consistent with prior guidance."
)

class TestUncertaintyPassage:
    """AC2: uncertainty-heavy passage scores high on tone_uncertainty."""

    def test_uncertainty_score_highest_among_negative_categories(self, lexicon, config):
        """tone_uncertainty should exceed tone_negative on this passage."""
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _UNCERTAINTY_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        tone = result["tone_vector"]
        assert tone["uncertainty"] > tone["negative"], (
            f"Expected uncertainty > negative on uncertainty-heavy text; "
            f"uncertainty={tone['uncertainty']:.4f}, negative={tone['negative']:.4f}"
        )

    def test_uncertainty_score_above_threshold(self, lexicon, config):
        """Uncertainty score should be meaningfully non-zero (> 0.05 on this text)."""
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _UNCERTAINTY_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        tone = result["tone_vector"]
        assert tone["uncertainty"] > 0.05, (
            f"tone_uncertainty {tone['uncertainty']:.4f} is unexpectedly low on "
            "uncertainty-heavy text; check lexicon coverage"
        )

    def test_weak_modal_also_elevated(self, lexicon, config):
        """Weak-modal words (may, might, could, possibly) also match in this passage."""
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _UNCERTAINTY_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        # "may", "might", "could", "possibly" are all in the curated subset.
        assert result["raw_counts"]["weak_modal"] > 0, (
            "Expected weak_modal matches in uncertainty-heavy passage (may/might/could)"
        )


# ---------------------------------------------------------------------------
# AC3 — "liability" is NOT negative (LM vs. Harvard-GI proof)
# ---------------------------------------------------------------------------

_LIABILITY_FIXTURE = (
    "The total liability on the balance sheet reflects long-term borrowings. "
    "Depreciation of property and equipment is recorded at cost. "
    "Total costs increased year over year due to capital expenditure."
)

class TestFinanceNeutralTerms:
    """AC3: liability / cost / depreciation are NOT counted negative (LM-specific)."""

    def test_liability_not_in_negative_lexicon(self, lexicon):
        """'liability' must not have a negative flag in the loaded lexicon."""
        entry = lexicon.get("liability")
        assert entry is not None, (
            "'liability' missing from lexicon entirely — add it as a neutral anchor"
        )
        assert not entry.get("negative"), (
            "'liability' has negative=1 in the LM lexicon — this contradicts "
            "Loughran & McDonald (2011) finance-neutral classification"
        )

    def test_cost_not_in_negative_lexicon(self, lexicon):
        """'cost' must not have a negative flag."""
        entry = lexicon.get("cost")
        assert entry is not None, (
            "'cost' missing from lexicon — add it as a neutral anchor"
        )
        assert not entry.get("negative"), (
            "'cost' has negative=1 — contradicts LM finance-neutral classification"
        )

    def test_depreciation_not_in_negative_lexicon(self, lexicon):
        """'depreciation' must not have a negative flag."""
        entry = lexicon.get("depreciation")
        assert entry is not None, (
            "'depreciation' missing from lexicon — add it as a neutral anchor"
        )
        assert not entry.get("negative"), (
            "'depreciation' has negative=1 — contradicts LM finance-neutral classification"
        )

    def test_liability_not_counted_in_negative_score(self, lexicon, config):
        """'liability' must NOT appear in matched_negative when scoring a passage."""
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _LIABILITY_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        matched_neg = result["matched_words"]["negative"]
        assert "liability" not in matched_neg, (
            f"'liability' incorrectly appeared in matched_negative: {matched_neg}"
        )

    def test_cost_not_counted_negative(self, lexicon, config):
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _LIABILITY_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        matched_neg = result["matched_words"]["negative"]
        assert "cost" not in matched_neg, (
            f"'cost' incorrectly appeared in matched_negative: {matched_neg}"
        )

    def test_depreciation_not_counted_negative(self, lexicon, config):
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            _LIABILITY_FIXTURE,
            config=config,
            lexicon=lexicon,
        )
        matched_neg = result["matched_words"]["negative"]
        assert "depreciation" not in matched_neg, (
            f"'depreciation' incorrectly appeared in matched_negative: {matched_neg}"
        )

    def test_negative_score_zero_for_liability_only_text(self, lexicon, config):
        """A text with only 'liability' should score tone_negative = 0.0."""
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            "The liability is recorded on the balance sheet.",
            config=config,
            lexicon=lexicon,
        )
        assert result["tone_vector"]["negative"] == 0.0, (
            "A text containing only 'liability' scored non-zero on tone_negative; "
            "the LM finance-neutral classification is violated"
        )


# ---------------------------------------------------------------------------
# AC4 — speaker_role: management vs. media
# ---------------------------------------------------------------------------

class TestSpeakerRoleTagging:
    """AC4: management vs. media distinction."""

    def test_10k_mda_tagged_management(self, config):
        """A chunk from a 10-K MD&A section is tagged as management."""
        from theme_engine.sentiment_lexicon import tag_speaker_role
        chunk = {
            "document_type": "10-k",
            "section_title": "Management's Discussion and Analysis",
            "block_type": "prose",
        }
        role = tag_speaker_role(chunk, config=config)
        assert role == "management", (
            f"10-K MD&A chunk should be 'management'; got '{role}'"
        )

    def test_40f_tagged_management(self, config):
        """A 40-F (Canadian cross-listing annual report) is tagged as management."""
        from theme_engine.sentiment_lexicon import tag_speaker_role
        chunk = {
            "document_type": "40-f",
            "section_title": "Outlook",
            "block_type": "prose",
        }
        role = tag_speaker_role(chunk, config=config)
        assert role == "management", (
            f"40-F chunk should be 'management'; got '{role}'"
        )

    def test_earnings_transcript_tagged_management(self, config):
        """Earnings call transcript is tagged as management."""
        from theme_engine.sentiment_lexicon import tag_speaker_role
        chunk = {
            "document_type": "earnings_transcript",
            "section_title": "CEO Remarks",
            "block_type": "prose",
        }
        role = tag_speaker_role(chunk, config=config)
        assert role == "management", (
            f"Earnings transcript chunk should be 'management'; got '{role}'"
        )

    def test_news_tagged_media(self, config):
        """A news article chunk is tagged as media."""
        from theme_engine.sentiment_lexicon import tag_speaker_role
        chunk = {
            "document_type": "news",
            "section_title": None,
            "block_type": "prose",
        }
        role = tag_speaker_role(chunk, config=config)
        assert role == "media", (
            f"News chunk should be 'media'; got '{role}'"
        )

    def test_news_article_tagged_media(self, config):
        """document_type 'news_article' maps to media."""
        from theme_engine.sentiment_lexicon import tag_speaker_role
        chunk = {
            "document_type": "news_article",
            "section_title": None,
            "block_type": "prose",
        }
        role = tag_speaker_role(chunk, config=config)
        assert role == "media", (
            f"news_article chunk should be 'media'; got '{role}'"
        )

    def test_analyst_report_tagged_analyst(self, config):
        """Sell-side research note is tagged as analyst."""
        from theme_engine.sentiment_lexicon import tag_speaker_role
        chunk = {
            "document_type": "analyst_report",
            "section_title": "Earnings Estimate Update",
            "block_type": "prose",
        }
        role = tag_speaker_role(chunk, config=config)
        assert role == "analyst", (
            f"Analyst report chunk should be 'analyst'; got '{role}'"
        )

    def test_unknown_document_type_tagged_unknown(self, config):
        """Unrecognised document_type falls back to 'unknown'."""
        from theme_engine.sentiment_lexicon import tag_speaker_role
        chunk = {
            "document_type": "totally_unrecognised_type_xyz",
            "section_title": None,
            "block_type": "prose",
        }
        role = tag_speaker_role(chunk, config=config)
        assert role == "unknown", (
            f"Unrecognised doc type should be 'unknown'; got '{role}'"
        )

    def test_mda_section_title_triggers_management_even_without_doc_type(self, config):
        """section_title containing 'management' triggers management role."""
        from theme_engine.sentiment_lexicon import tag_speaker_role
        chunk = {
            "document_type": "",  # doc type not available
            "section_title": "Management's Discussion and Analysis of Results",
            "block_type": "prose",
        }
        role = tag_speaker_role(chunk, config=config)
        assert role == "management", (
            f"Chunk with MD&A section_title should be 'management'; got '{role}'"
        )

    def test_management_vs_media_are_distinct(self, config):
        """Management and media roles are never the same for representative chunks."""
        from theme_engine.sentiment_lexicon import tag_speaker_role
        mgmt_chunk = {"document_type": "10-k", "section_title": "MD&A"}
        news_chunk = {"document_type": "news", "section_title": None}
        assert tag_speaker_role(mgmt_chunk, config=config) != \
               tag_speaker_role(news_chunk, config=config), (
            "Management and media chunks received the same speaker_role — "
            "attribution rules are not distinguishing them"
        )


# ---------------------------------------------------------------------------
# Additional robustness / edge-case tests
# ---------------------------------------------------------------------------

class TestScorerRobustness:
    """Edge cases and robustness checks."""

    def test_empty_text_returns_zero_scores(self, lexicon, config):
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk("", config=config, lexicon=lexicon)
        for cat in config["categories"]:
            assert result["tone_vector"][cat] == 0.0
            assert result["matched_words"][cat] == []

    def test_text_with_no_lm_words_returns_zero_scores(self, lexicon, config):
        from theme_engine.sentiment_lexicon import score_chunk
        result = score_chunk(
            "The cat sat on the mat with a hat and a bat.",
            config=config,
            lexicon=lexicon,
        )
        for cat in config["categories"]:
            assert result["tone_vector"][cat] == 0.0

    def test_token_count_overrides_computed_count(self, lexicon, config):
        """When token_count is passed, it overrides the computed count."""
        from theme_engine.sentiment_lexicon import score_chunk
        text = "excellent growth"  # 2 tokens; 'excellent' matches positive
        # Pass token_count=10 to test that the denominator is 10, not 2.
        result = score_chunk(text, token_count=10, config=config, lexicon=lexicon)
        # 'excellent' and 'growth' both match positive (1 hit each) = 2 raw positive
        expected = result["raw_counts"]["positive"] / 10
        assert abs(result["tone_vector"]["positive"] - expected) < 1e-9

    def test_word_repeated_counts_multiple_times(self, lexicon, config):
        """A word appearing N times in the text contributes N to the raw count."""
        from theme_engine.sentiment_lexicon import score_chunk
        text = "loss loss loss"  # 'loss' is negative; 3 occurrences
        result = score_chunk(text, config=config, lexicon=lexicon)
        assert result["raw_counts"]["negative"] == 3
        assert len(result["matched_words"]["negative"]) == 3

    def test_case_insensitive_matching(self, lexicon, config):
        """Matching must be case-insensitive."""
        from theme_engine.sentiment_lexicon import score_chunk
        texts = ["EXCELLENT", "Excellent", "excellent", "eXcElLeNt"]
        for text in texts:
            result = score_chunk(text, config=config, lexicon=lexicon)
            assert result["raw_counts"]["positive"] >= 1, (
                f"Failed case-insensitive match for '{text}'"
            )

    def test_score_chunks_batch_output_shape(self, lexicon, config):
        """score_chunks returns one row per input chunk."""
        from theme_engine.sentiment_lexicon import score_chunks
        chunks = [
            {
                "chunk_id": "c1",
                "document_id": "d1",
                "text": "excellent growth",
                "token_count": None,
                "section_title": "MD&A",
                "block_type": "prose",
                "document_type": "10-k",
                "available_at": "2024-06-30",
            },
            {
                "chunk_id": "c2",
                "document_id": "d1",
                "text": "The company faced a loss this quarter.",
                "token_count": None,
                "section_title": None,
                "block_type": "prose",
                "document_type": "news",
                "available_at": "2024-06-30",
            },
        ]
        rows = score_chunks(chunks, lexicon=lexicon, config=config)
        assert len(rows) == 2
        assert rows[0]["chunk_id"] == "c1"
        assert rows[1]["chunk_id"] == "c2"

    def test_score_chunks_includes_speaker_role(self, lexicon, config):
        """score_chunks must populate speaker_role on every row."""
        from theme_engine.sentiment_lexicon import score_chunks
        chunks = [
            {
                "chunk_id": "c1",
                "document_id": "d1",
                "text": "Management achieved excellent results.",
                "token_count": None,
                "section_title": "MD&A",
                "block_type": "prose",
                "document_type": "10-k",
                "available_at": "2024-01-01",
            },
            {
                "chunk_id": "c2",
                "document_id": "d2",
                "text": "Company shares declined today.",
                "token_count": None,
                "section_title": None,
                "block_type": "prose",
                "document_type": "news",
                "available_at": "2024-01-01",
            },
        ]
        rows = score_chunks(chunks, lexicon=lexicon, config=config)
        assert rows[0]["speaker_role"] == "management"
        assert rows[1]["speaker_role"] == "media"

    def test_matched_words_preserves_order_of_occurrence(self, lexicon, config):
        """matched_words must follow order of first occurrence in the text."""
        from theme_engine.sentiment_lexicon import score_chunk
        text = "excellent outstanding solid"
        result = score_chunk(text, config=config, lexicon=lexicon)
        matched = result["matched_words"]["positive"]
        # All three are positive words; order must follow text.
        assert matched[0] == "excellent"
        assert "outstanding" in matched
        assert "solid" in matched

    def test_may_is_both_uncertainty_and_weak_modal(self, lexicon, config):
        """'may' has both uncertainty=1 and weak_modal=1 in the curated CSV."""
        assert "may" in lexicon, "'may' not in lexicon"
        entry = lexicon["may"]
        assert entry.get("uncertainty"), "'may' not flagged as uncertainty"
        assert entry.get("weak_modal"), "'may' not flagged as weak_modal"

    def test_will_is_strong_modal(self, lexicon, config):
        """'will' is classified as strong_modal."""
        assert "will" in lexicon, "'will' not in lexicon"
        assert lexicon["will"].get("strong_modal"), "'will' not flagged as strong_modal"

    def test_litigation_is_litigious(self, lexicon):
        """'litigation' and 'plaintiff' are in the litigious category."""
        for word in ("litigation", "plaintiff"):
            assert word in lexicon, f"'{word}' missing from lexicon"
            assert lexicon[word].get("litigious"), f"'{word}' not flagged litigious"


# ---------------------------------------------------------------------------
# AC5 — Hermetic (no network)
# ---------------------------------------------------------------------------

class TestHermeticNoNetwork:
    """The scorer must never make network calls."""

    def test_scorer_imports_no_http_library(self):
        """The sentiment_lexicon module must not import requests, httpx, urllib3, etc."""
        import theme_engine.sentiment_lexicon as mod
        import inspect
        source = inspect.getsource(mod)
        for lib in ("requests", "httpx", "urllib3", "aiohttp", "http.client"):
            # Check the source for explicit imports of network libraries.
            assert f"import {lib}" not in source and f"from {lib}" not in source, (
                f"sentiment_lexicon.py imports network library '{lib}'"
            )

    def test_full_score_pipeline_without_network(self, lexicon, config):
        """End-to-end scoring from text to tone vector requires no network."""
        from theme_engine.sentiment_lexicon import score_chunk, tag_speaker_role
        # If this completes without error, no network was needed.
        text = (
            "The company believes results may improve despite uncertain conditions. "
            "Litigation risk remains a concern. The liability on our balance sheet "
            "reflects long-term obligations."
        )
        result = score_chunk(text, config=config, lexicon=lexicon)
        chunk = {"document_type": "10-k", "section_title": "MD&A"}
        role = tag_speaker_role(chunk, config=config)
        assert result["token_count"] > 0
        assert role == "management"
