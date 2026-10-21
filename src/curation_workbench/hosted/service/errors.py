"""Typed errors of the hosted API (snapgate `ServiceError`s; `routes.errors` maps them)."""
from __future__ import annotations

from snapgate import Conflict, Forbidden, ServiceError
from snapgate.useradmin import NotAdmin, SessionRequired


class NotSynced(ServiceError):
    """503: the project has no published snapshot yet (plan 4's worker)."""
    status, code = 503, "not_synced"


class BadRequest(ServiceError):
    """400: a malformed request the store never sees (bad body, bad If-Match)."""
    status, code = 400, "invalid"


class PreconditionRequired(ServiceError):
    """428: a write to a decision needs `If-Match: <rev>`."""
    status, code = 428, "precondition_required"


class NotVoter(Forbidden):
    code = "not_voter"


class NotCurator(Forbidden):
    code = "not_curator"


class QuestionResolved(Conflict):
    code = "question_resolved"


class AccountNotApproved(Conflict):
    code = "account_not_approved"


class DecisionStillActive(Conflict):
    code = "decision_still_active"


__all__ = ["AccountNotApproved", "BadRequest", "DecisionStillActive", "NotAdmin", "NotCurator",
           "NotSynced", "NotVoter", "PreconditionRequired", "QuestionResolved", "SessionRequired"]
