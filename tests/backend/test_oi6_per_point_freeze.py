"""OI-6 R1-2: per-point freeze isolation, run-level flip, tamper, idempotence.

Freezing one point hashes only that point's artifacts (keys
discovery/<as_of>/<name>) and records it in discovery_frozen_points without
flipping the run-level discovery_frozen flag until every authored point is
frozen.  Tampering one point's artifact after freeze is detected for that point
only; per-point freeze is idempotent.
"""

from __future__ import annotations

import pytest

from theme_engine import freeze as freeze_mod, runs
from theme_engine.models import RunCreateRequest

T1 = "2024-03-31"
T2 = "2024-06-30"


def _seed_point(run_id: str, as_of: str) -> None:
    d = runs.discovery_point_dir(run_id, as_of, for_write=True)
    for name in runs.REQUIRED_DISCOVERY_ARTIFACTS:
        # Freeze only hashes bytes; content need not parse. Stamp the point so
        # the two points hash to different digests.
        (d / name).write_text(f"seed-{as_of}-{name}", encoding="utf-8")


def _keys_for(manifest, as_of: str) -> set[str]:
    prefix = f"discovery/{as_of}/"
    return {k for k in (manifest.discovery_artifact_hashes or {}) if k.startswith(prefix)}


def test_per_point_freeze_isolation_and_run_level_flip():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id
    _seed_point(run_id, T1)
    _seed_point(run_id, T2)

    # Freeze t1 only.
    m = freeze_mod.freeze_discovery(run_id, as_of=T1)
    assert set(m.discovery_frozen_points or {}) == {T1}
    assert m.discovery_frozen is False  # run-level NOT flipped yet
    t1_keys = _keys_for(m, T1)
    assert t1_keys, "t1 hash keys must be present"
    assert all(k.startswith(f"discovery/{T1}/") for k in t1_keys)
    assert _keys_for(m, T2) == set()  # t2 not frozen → no keys
    assert f"discovery/{T1}/graph.json" in (m.discovery_artifact_hashes or {})

    # Freeze t2 → run-level flips True, t1 keys untouched.
    m2 = freeze_mod.freeze_discovery(run_id, as_of=T2)
    assert set(m2.discovery_frozen_points or {}) == {T1, T2}
    assert m2.discovery_frozen is True
    assert m2.frozen_at is not None
    assert _keys_for(m2, T1) == t1_keys  # t1 keys preserved across t2 freeze
    assert _keys_for(m2, T2)


def test_per_point_tamper_detected_only_for_that_point():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id
    _seed_point(run_id, T1)
    _seed_point(run_id, T2)
    freeze_mod.freeze_discovery(run_id, as_of=T1)
    manifest = freeze_mod.freeze_discovery(run_id, as_of=T2)

    # Tamper t1's graph.json after both points are frozen.
    (runs.discovery_point_dir(run_id, T1) / "graph.json").write_text("TAMPERED", encoding="utf-8")

    # t1 mismatch raises; t2 still verifies clean.
    with pytest.raises(ValueError, match="hash mismatch"):
        runs._ensure_discovery_hashes_match(manifest, run_id, as_of=T1)
    runs._ensure_discovery_hashes_match(manifest, run_id, as_of=T2)  # no raise


def test_per_point_freeze_idempotent():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id
    _seed_point(run_id, T1)

    m1 = freeze_mod.freeze_discovery(run_id, as_of=T1)
    keys1 = dict(m1.discovery_artifact_hashes or {})
    m2 = freeze_mod.freeze_discovery(run_id, as_of=T1)
    keys2 = dict(m2.discovery_artifact_hashes or {})
    assert keys1 == keys2  # recomputes identical hashes


def test_bulk_freeze_rejected_on_multi_point_run():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id
    _seed_point(run_id, T1)
    with pytest.raises(ValueError, match="as_of required"):
        freeze_mod.freeze_discovery(run_id, as_of=None)
