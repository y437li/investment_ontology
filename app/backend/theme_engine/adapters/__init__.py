"""Offline data-source adapters that emit source_manifest.csv rows.

Each adapter is a deterministic OFFLINE parser over local fixture inputs.
Adapters never touch the network and never write under data/. They produce
rows compatible with ``data_import.REQUIRED_MANIFEST_COLUMNS`` (including the
``vintage`` field) so the existing ``/api/data/import`` endpoint can ingest
them into a run's ``discovery/`` subdir.
"""
