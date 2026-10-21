# Hosted API (`cwb hosted serve`)

The hosted, multi-user curation service. `frontend/src/api/hosted.ts` is the
only client module that knows these routes. Base URL: `https://curate.databio.org`
(dev: `http://127.0.0.1:8134`).

## Who may call

- **Sign-in** is snapgate through Prosopia (`/auth/*`, below). A browser
  session holds every scope. An agent uses a Settings-page API key:
  `Authorization: Bearer cwb_<key>`.
- **Invite-only.** A new account is PENDING until a superadmin approves it
  (`POST /auth/admin/users/{id}/approve`). Until then every `/api/v1` route
  answers 403 `user_not_approved`.
- **Teams.** A superadmin puts approved accounts on a project's team as
  `voter` or `curator` (a curator can also vote). A project you are not on is
  404, never 403.
- **Scopes** (keys): `curation:read`; `curation:vote` (includes read);
  `curation:decide` (includes read, curators only, opt-in per key). New keys
  get read + vote. A missing scope is 403 `insufficient_scope`.
- **Superadmin** = `SNAPGATE_SUPERADMIN_EMAILS`, browser session only. It
  reads every project but votes and decides only where it is on the team.
  Admin routes answer a key 403 `session_required` and any other session 403
  `not_admin`.

| Caller | project reads | votes | decision writes, resolve | admin |
|---|---|---|---|---|
| anonymous | 401 | 401 | 401 | 401 |
| not on the team | 404 | 404 | 404 | 403 `not_admin` |
| voter (session or key) | yes | yes | 403 `not_curator` | 403 |
| curator session, or key with `decide` | yes | yes | yes | 403 |
| curator key without `decide` | yes | yes | 403 `insufficient_scope` | 403 `session_required` |
| superadmin session, not on the team | yes | 403 `not_voter` | 403 `not_curator` | yes |

## Errors

Every error body is `{"detail": str, "code": str}`. A store error adds
`"errors": [str, ...]` (every message; `detail` is the first) and, on a 409
from a revision check, `"current"` (the current record or vote).

| Status | code |
|---|---|
| 400 | `invalid` (bad record, vote or body) |
| 401 | `invalid_token` / `unauthorized` |
| 403 | `user_not_approved`, `not_voter`, `not_curator`, `insufficient_scope`, `not_admin`, `session_required` |
| 404 | `not_found` |
| 409 | `conflict` (subject changed, stale rev, duplicate), `question_resolved`, `account_not_approved`, `decision_still_active` |
| 422 | `invalid_input` (FastAPI body/query validation; `detail` is a list) |
| 428 | `precondition_required` (missing `If-Match`) |
| 429 | `rate_limited` |
| 503 | `not_synced` (the project has no published snapshot yet) |

## No sign-in

- `GET /health` -> 200 `{"status": "ok", "checks": {"db": "ok", "schema": "ok"}}`, or 503 `degraded` with the failing check.
- `GET /api/config` -> `{"mode": "hosted"}`.
- `POST /api/v1/hooks/github/{slug}`: GitHub push webhook (HMAC `X-Hub-Signature-256` with the resident's secret). 202 queued or ignored, 200 `ping`, 401 bad signature, 404 no webhook, 429 over 30/minute.

## Sign-in and accounts (snapgate, `/auth/*`)

`GET /auth/login` (to Prosopia), `GET /auth/login/callback` (the redirect URI
registered in Prosopia), `POST /auth/exchange`, `GET /auth/me`,
`POST /auth/refresh`, `POST /auth/logout`, `GET|POST|DELETE /auth/api-keys`,
`GET /auth/scopes`; dev only: `POST /auth/demo-login/admin|user`.
Site admins (browser session): `GET /auth/admin/users[?status=pending|approved|suspended&q=]`
-> `{"users": [{id, email, name, status, site_role, ...}], "total"}`,
`POST /auth/admin/users/{id}/approve|suspend|unsuspend`, `GET /auth/admin/logins`.
Errors under `/auth/` keep snapgate's body `{"detail": {"error", "message"}, "code"}`.

## Me and projects (any approved caller)

- `GET /api/v1/me` -> `{"user": {"id", "name", "email"}, "site_role": "superadmin"|"admin"|null, "memberships": {"<slug>": "voter"|"curator"}}` (`site_role` is null for a key).
- `GET /api/v1/projects` -> `{"projects": [{"slug", "name", "role", "commit", "schema_fingerprint", "synced_at"}]}`: your projects (every live one for a superadmin session; `role` null where not on the team). `commit`/`synced_at` are null before the first sync.
- `GET /api/v1/projects/{slug}` -> `{"slug", "name", "role", "commit", "branch", "schema_fingerprint", "synced_at", "decision_counts": {"pending", "applied", "withdrawn"}, "open_questions"}` (hosted decisions only).
- `GET /api/v1/projects/{slug}/team` -> `{"members": [{"user_id", "name", "email", "role"}]}`.

## Read routes (team members)

Each body is the `curation_workbench.views` function in brackets, from the project's published snapshot,
its derived status/review, and `records` = the repo's decisions (from the
snapshot) followed by the hosted ones. SIDs go in the query string. 503
`not_synced` before the first snapshot.

- `GET .../{slug}/review` [`review_index`]: review.json without items; each type keeps `sids`.
- `GET .../{slug}/review/{track}/{type}` [`review_type_page`]: `{track, type, title, decisions_next_id, items}`; `decisions_next_id` is `H-NNNN`.
- `GET .../{slug}/subject_index` [`subject_index`].
- `GET .../{slug}/subject?sid=` [`subject_view`]: plus `"votes": [Vote]` (current votes on this subject, every review type).
- `GET .../{slug}/verdicts` [`verdicts_view`].
- `GET .../{slug}/findings` [`findings_view`].
- `GET .../{slug}/history` [`history`]: repo and hosted decisions, newest first.
- `GET .../{slug}/pending` [`pending_view`]: the Approved page. `{"decisions": [pending hosted record + effects: null], "errors": {id: [plugin check error]}, "ok", "applied": [hosted record], "repository": {"url", "workflow_url", "commit", "commit_url", "branch", "synced_at"}}`. `applied` is the latest 20 hosted decisions that reached the repo (newest first; `applied.repo_id` is the `DEC-` id there, `applied.commit` the commit that brought it); `workflow_url` is `<repo>/actions/workflows/curate.yml` for github.com repos, else null. No `files` key (the site never runs the apply engine).

## Decisions

A **record** is the `decisions.yaml` record (`id`, `subject`, `also_affects`,
`verdict`, `operation`, `rationale`, `evidence`, `decided_by`, `decided_on`,
`subject_hashes`, `schema_fingerprint`, `status`, `applied`, `supersedes`,
`origin`) plus `rev`, `vote_ids`, `stale`, `stale_reason`, `stale_new_sid`
and `plugin_errors` (the worker's plugin checks; `[]` when none or not yet
checked). Hosted ids are `H-NNNN` per project, `origin: {"kind": "hosted", "ref": <id>}`.

- `GET .../{slug}/decisions[?status=pending]` (member) -> `{"version": 1, "next_id": "H-NNNN", "decisions": [record], "token"}`. Hosted records only.
- `GET .../{slug}/decisions/{id}` (member) -> `{"decision": record}`.
- `GET .../{slug}/decisions/export` (member; a `curation:read` key is enough) -> `{"decisions": [plain record], "held": [{"id", "reason"}], "commit"}`: what `cwb pull` applies. `decisions` are the approved (pending) hosted decisions that are safe to apply now, oldest first, without `rev`, vote links, stale marks or plugin errors; stale ones and ones that fail the worker's plugin checks are in `held` with a plain reason instead. `commit` is the last-synced commit.
- `POST .../{slug}/decisions` (curator + `decide`) -> 201 `{"decision": record}`. Body: `subject`, `verdict`, `operation`, `rationale`, `also_affects`, `evidence`, `subject_hashes`, `supersedes` (a hosted `H-` id or a repo `DEC-` id). The server sets `id`, `decided_by` ("Name <email>"), `decided_on` (today), `schema_fingerprint` (current snapshot), `status: pending`, `applied`, `origin`; client values for these are dropped. 400 invalid (the shared validator's messages), 409 a subject changed since `subject_hashes` was read.
- `PUT .../{slug}/decisions/{id}` (curator + `decide`), header `If-Match: <rev>` (else 428) -> `{"decision": record}`; pending records only; 409 on a stale rev (with `current`).
- `DELETE .../{slug}/decisions/{id}` (curator + `decide`), `If-Match: <rev>` -> `{"decision": record}` with `status: withdrawn` (records are never removed).

## Votes and questions

A **question** is one subject in one review type: `(sid, review_type)`, where
`review_type` is `"<track>/<type>"` or `"subject"` (a vote from a subject page,
whose `answer` must equal its proposal's verdict).

`Vote = {"id", "sid", "review_type", "answer", "proposal", "comment", "user": {"id", "name"}, "subject_hash", "stale", "stale_reason", "rev", "created_at", "updated_at", "former_member"}`

`Question = {"sid", "review_type", "state": "open"|"resolved", "outcome": "decision"|"no_action"|null, "decision_id": "H-NNNN"|null, "answers": [{"answer", "count", "stale", "vote_ids", "proposal"}], "total", "stale", "mine": {"vote_id", "answer", "proposal", "comment", "stale"}|null}`.
`count`, `total` and `vote_ids` cover counted votes only: not stale (plan 4's
worker marks a vote stale when its subject changed; voting again clears it)
and not from a former team member. `stale` counts stale votes. `proposal` is
the oldest counted vote's proposal for that answer. Answers are sorted by
count, then name.

- `GET .../{slug}/questions[?review_type=&state=open|resolved&sid=]` (member) -> `{"questions": [Question]}`: every question with votes or a resolution.
- `GET .../{slug}/votes?sid=&review_type=` (member) -> `{"votes": [Vote]}`: who voted what on one question.
- `PUT .../{slug}/votes` (voter + `vote`) -> `{"vote": Vote}`. Body `{"sid", "review_type", "answer", "subject_hash", "proposal"?: {"verdict", "operation"?, "rationale"?, "also_affects"?, "evidence"?}, "comment"?}`. Upserts your one vote on the question. 400 invalid, 409 a changed `subject_hash`, 409 `question_resolved`.
- `DELETE .../{slug}/votes?sid=&review_type=` (voter + `vote`) -> 204. 404 no vote, 409 used by a decision or `question_resolved`.
- `POST .../{slug}/questions/resolve` (curator + `decide`) -> `{"question": Question, "decision": record|null}`. Body `{"sid", "review_type", "outcome": "decision"|"no_action", "from_vote"?: vote id, "decision"?: client record, "note"?}`. A `decision` outcome needs exactly one of `from_vote` (that vote's proposal, on this question) or `decision` (a full client record about this subject; the curator may edit it first). The decision links every counted vote (`vote_ids`) and gets one more evidence entry `{"kind": "other", "detail": "votes: keep 3, delete 1 (2 stale not counted)"}`. All or nothing: a validation failure writes nothing and leaves the question open.
- `POST .../{slug}/questions/reopen` (curator + `decide`) -> `{"question": Question}`. Body `{"sid", "review_type"}`. 409 if not resolved; 409 `decision_still_active` until its decision is withdrawn.

## Admin (superadmin browser session)

- `GET /api/v1/admin/projects` -> `{"projects": [{"slug", "name", "archived_at", "created_at", "commit", "synced_at", "members"}]}` (archived included).
- `POST /api/v1/admin/projects` body `{"slug", "name"}` -> 201 `{"project"}`; slug `^[a-z0-9][a-z0-9-]{1,40}$` (400), duplicate 409.
- `PATCH /api/v1/admin/projects/{slug}` body `{"name"}` -> `{"project"}`.
- `DELETE /api/v1/admin/projects/{slug}` -> `{"project"}` (archived: hidden, kept).
- `PUT /api/v1/admin/projects/{slug}/team/{user_id}` body `{"role": "voter"|"curator"}` -> `{"member": {"user_id", "name", "email", "role"}}`. The account must be APPROVED (409 `account_not_approved`); a removed member comes back.
- `DELETE /api/v1/admin/projects/{slug}/team/{user_id}` -> 204. Their votes stay, shown with `former_member: true`, not counted.
- `GET /api/v1/admin/residents`, `GET /api/v1/admin/residents/{slug}`: plan 4's `residents.list_residents` / `show` (read-only; resident changes are `cwb hosted resident ...`).
