"""API key scopes, handed to snapgate at setup.

A browser session holds every scope. Settings-page keys get `curation:read`
and `curation:vote` unless the person ticks `curation:decide`. Team, project
and resident admin is browser-session only, never a key.
"""
from __future__ import annotations

from snapgate import ScopeConfig, ScopeGroup

READ = "curation:read"
VOTE = "curation:vote"
DECIDE = "curation:decide"

ALL_SCOPES = (READ, VOTE, DECIDE)
DEFAULT_SCOPES = (READ, VOTE)

SCOPES = ScopeConfig(
    scopes={
        READ: "Read the projects you are on: their review queue, votes, tallies and decisions.",
        VOTE: "Cast and retract your own votes.",
        DECIDE: "Create, edit and withdraw decisions and resolve questions, where you are a curator.",
    },
    implies={VOTE: [READ], DECIDE: [READ]},
    groups=[
        ScopeGroup("read", "Read projects", (READ,)),
        ScopeGroup("vote", "Vote", (VOTE,)),
        ScopeGroup("decide", "Decide (curators)", (DECIDE,)),
    ],
    default_scopes=DEFAULT_SCOPES,
)

__all__ = ["ALL_SCOPES", "DECIDE", "DEFAULT_SCOPES", "READ", "SCOPES", "VOTE"]
