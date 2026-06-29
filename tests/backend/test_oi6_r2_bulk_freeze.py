"""OI-6 R2: bulk freeze of all authored points via freeze.freeze_all_points.

freeze_all_points loops every authored point, reusing the per-point freeze
(merge + run-level flip).  Result: run-level discovery_frozen=True,
discovery_frozen_points covers all points, frozen_at set, per-point hash keys
present, and the operation is idempotent.
"""

from __future__ import annotations

from theme_engine import freeze as freeze_mod, run_cache, runs
from theme_engine.models import RunCreateRequest

T1 = "2024-03-31"
T2 = "2024-06-30"
T3 = "2024-09-30"


def _seed_point(run_id: str, as_of: str) -> None:
    d = runs.discovery_point_dir(run_id, as_of, for_write=True)
    for name in runs.REQUIRED_DISCOVERY_ARTIFACTS:
        (d / name).write_text(f"seed-{as_of}-{name}", encoding="utf-8")


def test_bulk_freeze_freezes_all_points():
    run_cache.clear_frozen_cache()
    run = runs.create_run(
        RunCreateRequest(as_of_date=T3, as_of_dates=[T1, T2, T3])
    )
    rid = run.run_id
    for p in (T1, T2, T3):
        _seed_point(rid, p)

    m = freeze_mod.freeze_all_points(rid)

    run_cache.clear_frozen_cache()
    assert m.discovery_frozen is True
    assert set(m.discovery_frozen_points or {}) == {T1, T2, T3}
    assert m.frozen_at is not None

    # Hash keys are namespaced per point: discovery/<T>/<name>.
    hashes = m.discovery_artifact_hashes or {}
    for p in (T1, T2, T3):
        assert f"discovery/{p}/graph.json" in hashes
        assert f"discovery/{p}/communities.json" in hashes

    # Idempotent: re-running recomputes identical hashes + flags.
    m2 = freeze_mod.freeze_all_points(rid)
    assert (m2.discovery_artifact_hashes or {}) == hashes
    assert m2.discovery_frozen is True
    assert set(m2.discovery_frozen_points or {}) == {T1, T2, T3}


def test_bulk_freeze_legacy_flat_run_delegates():
    run_cache.clear_frozen_cache()
    run = runs.create_run(RunCreateRequest(as_of_date=T1))  # no as_of_dates
    rid = run.run_id
    d = runs.discovery_point_dir(rid, None, for_write=True)
    for name in runs.REQUIRED_DISCOVERY_ARTIFACTS:
        (d / name).write_text(f"seed-flat-{name}", encoding="utf-8")

    m = freeze_mod.freeze_all_points(rid)
    run_cache.clear_frozen_cache()
    assert m.discovery_frozen is True
    hashes = m.discovery_artifact_hashes or {}
    assert "discovery/graph.json" in hashes  # flat keys, not per-point
