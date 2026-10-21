"""Hosted (multi-user) mode. Needs the `hosted` extra.

  hosted.sync          resident projects, the sync worker and its tables (plan 4)
  hosted.app           `create_app(engine=, settings=)`: the API service (plan 2)
  hosted.asgi          the uvicorn target (`cwb hosted serve`)
  hosted.config        `Settings` (CWB_* env)
  hosted.auth          snapgate sign-in (Prosopia), invite-only hooks, `CurrentCaller`
  hosted.scopes        API key scopes
  hosted.project_data  a project's published snapshot + derived data (`load`)
  hosted.service       every access rule (roles, projects, data, decisions, votes)
  hosted.routes        `/api/v1` adapters over the service layer
  hosted.cli           `cwb hosted serve | admin ...` (registered by curation_workbench.cli)

See hosted/API.md. Nothing in this package is imported by local mode or by
the isolated runner (`cwb` imports `hosted.cli` only to add its parser, and
that module imports nothing heavy at module level).
"""
