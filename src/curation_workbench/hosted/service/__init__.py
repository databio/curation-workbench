"""Every access rule of the hosted API. Routes are thin adapters over these.

Each function takes the engine and a `snapgate.Caller`, checks the caller's
scope first (`caller.require(...)`), then the team role (`roles.access`), and
raises typed errors (`errors`, snapgate's `ServiceError`s, or the store's
`StoreError`), which `routes.errors` maps to `{detail, code}` bodies.

  errors     the hosted error types (not_synced, not_voter, not_curator, ...)
  roles      site role (snapgate) and team role (`project_members`)
  projects   me, project list and detail, team, project and team admin
  data       read routes, built with `curation_workbench.views`
  decisions  hosted decision create/update/withdraw/export
  votes      votes, questions (tallies), resolve, reopen
"""
