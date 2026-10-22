"""Project repos and their committed snapshots.

  tables      SQLAlchemy Core tables mirroring 041_repo_snapshots.sql (`METADATA`)
  github      read a public GitHub repo over HTTPS: branch head, one file at a commit
  repos       register a project's repo, fetch its snapshot (`sync_project`), the poller
  publish     `publish_snapshot`, the bundle cache, `refresh_stale` after each write
  stale       staleness rules for open votes and pending hosted decisions

The project repo builds and commits its snapshot file itself (`cwb regen`);
the site only downloads it. Nothing here imports or runs project code. See
dev-docs/sync.md. Importing this package loads nothing heavy.
"""
