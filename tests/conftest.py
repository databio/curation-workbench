"""Reproduction tests against a real ONGA checkout.

Set ONGA_ROOT to an ONGA tree (default: ../onga next to this repo). Tests that
write never touch it: they copy what they need into pytest's tmp_path, or use
ONGA_SCRATCH (a disposable ONGA worktree) for whole-tree runs such as apply.
Comparisons ignore only `_generated_by` strings.
"""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ADAPTER = REPO / "adapters" / "onga" / "workbench.yaml"


def onga_root():
    return Path(os.environ.get("ONGA_ROOT", REPO.parent / "onga")).resolve()


@pytest.fixture(scope="session")
def onga():
    root = onga_root()
    if not (root / "src" / "onga.yaml").exists():
        pytest.skip(f"no ONGA tree at {root} (set ONGA_ROOT)")
    return root


@pytest.fixture(scope="session")
def scratch():
    path = os.environ.get("ONGA_SCRATCH")
    if not path or not Path(path, "src", "onga.yaml").exists():
        pytest.skip("set ONGA_SCRATCH to a disposable ONGA worktree")
    return Path(path).resolve()


def legacy(root, name):
    """Import ONGA's own module `scripts/<name>` (e.g. 'workbench.store') from `root`."""
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module(name)


def same_json(a_text, b_text):
    a, b = json.loads(a_text), json.loads(b_text)
    for d in (a, b):
        if isinstance(d, dict):
            d.pop("_generated_by", None)
    return a == b


def strip_generated(text):
    return "\n".join(ln for ln in text.splitlines() if '"_generated_by"' not in ln)
