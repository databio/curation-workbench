"""Every finding of a snapshot bundle, with whether it is still open.

`collect(bundle, status) -> {id: finding}`: the bundle's findings (lint plus
plugin findings sources, gathered once at snapshot time), each a copy with
`open: bool`, true when any subject's status.json `open_findings` lists it.
Generic and decision-free apart from `status`, so derive and the read views
(views.py) use it with no plugin code.
"""


def collect(bundle, status):
    open_ids = {fid for r in status["subjects"].values() for fid in r.get("open_findings") or []}
    return {f["id"]: {**f, "open": f["id"] in open_ids} for f in bundle["findings"]}
