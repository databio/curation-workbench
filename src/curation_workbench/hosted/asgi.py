"""The uvicorn target: `curation_workbench.hosted.asgi:app`.

Kept out of `hosted.app` so importing the factory (tests) never reads env
config or builds an engine.
"""
from .app import create_app

app = create_app()
