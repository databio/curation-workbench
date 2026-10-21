"""Stage 1: the subject registry reproduces ONGA's curation/*.json byte for byte."""
from conftest import ADAPTER, strip_generated

from curation_workbench import subjects
from curation_workbench.workbench import Workbench


def test_registry_matches(onga, tmp_path):
    wb = Workbench.open(ADAPTER, root=onga, out=tmp_path)
    files, counts = subjects.render(wb)
    assert counts["total"] > 0
    for path, text in files.items():
        want = (onga / "curation" / path.name).read_text()
        assert strip_generated(text) == strip_generated(want), path.name
