import sys
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq
import pytest
BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path: sys.path.insert(0, str(BACKEND))
from theme_engine import source, runs
from theme_engine.config import settings
from theme_engine.models import RunCreateRequest

def test_chunk_source_returns_full_doc_and_attribution():
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir)/run.run_id/"discovery"; d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"chunk_id":["c1","c2"],"document_id":["d1","d1"],"raw_document_id":["r1","r1"],
        "chunk_index":[0,1],"text":["First part.","Second part."],"section_title":["Intro","Intro"],
        "available_at":["2024-01-01","2024-01-01"]}), d/"chunks.parquet")
    pq.write_table(pa.table({"document_id":["d1"],"raw_document_id":["r1"],"source":["Reuters"],
        "title":["Big News"],"published_at":["2024-01-01"],"document_type":["news"]}), d/"documents.parquet")
    pq.write_table(pa.table({"document_id":["r1"],"source_url":["http://x/y"],"title":["Big News"]}), d/"raw_documents.parquet")
    out = source.chunk_source(run.run_id, "c1")
    assert out["chunk_text"] == "First part."
    assert out["document_text"] == "First part.\nSecond part."   # whole doc, in order
    assert out["document"]["title"] == "Big News" and out["document"]["source_url"] == "http://x/y"
    with pytest.raises(ValueError):
        source.chunk_source(run.run_id, "nope")
