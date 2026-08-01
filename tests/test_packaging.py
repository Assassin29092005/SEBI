"""Pins backend/pyproject.toml's [tool.setuptools.package-data] declaration
against what's actually on disk.

The bug this guards against: a real (non-editable) install — `pip install
./backend`, or the Docker image (backend/Dockerfile) built from it — drops
any non-.py file under app/ that isn't declared here. `pip install -e`
never surfaces this: an editable install just points back at the source
tree, where the files are already sitting on disk regardless of what's
declared. This was a real, previously-invisible bug: app.schema.loader
.load_checklist() runs at app.main import time, so a real install imported
cleanly then crashed with SchemaIntegrityError the moment anything touched
the checklist — verified by hand while building the CI/CD Docker images,
where the failure mode would otherwise have first appeared as a crash-
looping production container.

No pip/build tooling needed: both checks below just compare glob patterns
against the real filesystem.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
APP_DIR = BACKEND_DIR / "app"


def _package_data_patterns() -> list[str]:
    with (BACKEND_DIR / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)
    return pyproject["tool"]["setuptools"]["package-data"]["app"]


def test_package_data_patterns_match_files_that_actually_exist() -> None:
    """Every declared pattern must match at least one real file — a stale or
    typo'd pattern would otherwise pass CI while still shipping nothing."""
    for pattern in _package_data_patterns():
        matches = list(APP_DIR.glob(pattern))
        assert matches, f"package-data pattern {pattern!r} matches no files under {APP_DIR}"


def test_every_non_py_file_under_app_is_covered_by_package_data() -> None:
    """The inverse check: no runtime data file is missing from the declaration."""
    declared: set[Path] = set()
    for pattern in _package_data_patterns():
        declared.update(p.resolve() for p in APP_DIR.glob(pattern))

    actual_data_files = {
        p.resolve()
        for p in APP_DIR.rglob("*")
        if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts
    }
    undeclared = actual_data_files - declared
    assert not undeclared, f"data files not covered by [tool.setuptools.package-data]: {undeclared}"
