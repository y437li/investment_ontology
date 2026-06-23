"""Sentence/paragraph-aware chunking (v2): never cut mid-sentence, keep overlap."""
import sys
from pathlib import Path
BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path: sys.path.insert(0, str(BACKEND))
from theme_engine.chunking import _split_text, CHUNK_SIZE_CHARS


def test_chunks_never_cut_mid_sentence_and_overlap():
    text = " ".join(f"Sentence {i} about oil prices and Suncor refining margins." for i in range(40))
    chunks = _split_text(text)
    assert len(chunks) > 1
    assert all((e - s) <= CHUNK_SIZE_CHARS for s, e, _ in chunks)          # respect the window
    assert all(t.strip().endswith(".") for _, _, t in chunks)             # whole sentences only
    assert chunks[1][0] < chunks[0][1]                                     # overlap present
    assert all(b[0] > a[0] for a, b in zip(chunks, chunks[1:]))           # strictly progresses


def test_single_long_sentence_taken_whole():
    long = "word " * 400 + "end."                                          # > window, no boundary
    chunks = _split_text(long)
    assert len(chunks) == 1 and chunks[0][2] == long


def test_empty_text():
    assert _split_text("") == []
