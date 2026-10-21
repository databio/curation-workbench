"""Stage 4: schema lint reproduces ONGA's curation/findings/schema_lint.json."""
from conftest import ADAPTER, strip_generated

from curation_workbench import lint
from curation_workbench.workbench import Workbench


def test_lint_matches(onga):
    wb = Workbench.open(ADAPTER, root=onga)
    text, summary = lint.render(wb)
    want = (onga / "curation" / "findings" / "schema_lint.json").read_text()
    assert strip_generated(text) == strip_generated(want), summary
