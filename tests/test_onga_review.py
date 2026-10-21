"""Stage 6: review.json sorts ONGA's queue into the same tracks and types the
ONGA site's TypeScript did (counts recorded from the live site at HEAD)."""
from conftest import ADAPTER

from curation_workbench import review
from curation_workbench.workbench import Workbench

EXPECTED = {
    "vocabulary": {"links": 165, "duplicates": 45, "keep-or-cut": 31, "definitions": 17, "signoff": 73},
    "schema": {"fields": 35, "definitions": 9, "links": 11, "structure": 1, "signoff": 90},
}


def test_review_counts(scratch):
    wb = Workbench.open(ADAPTER, root=scratch)
    data = review.build(wb)
    got = {t["id"]: {ty["id"]: ty["count"] for ty in t["types"]} for t in data["tracks"]}
    assert data["version"] == 2 and got == EXPECTED
    assert sum(t["total"] for t in data["tracks"]) == 396
