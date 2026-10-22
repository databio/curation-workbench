"""The local CLI loads no SQLAlchemy, and the hosted site never loads the code
that imports a project's plugins."""
import subprocess
import sys

FORBIDDEN = ("sqlalchemy", "psycopg", "snapgate", "fastapi", "curation_workbench.hosted")


def loaded_after(code):
    probe = (f"import sys\n{code}\n"
             f"print('\\n'.join(m for m in sys.modules if m.startswith({FORBIDDEN!r})))")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    return [m for m in out.stdout.split() if m]


def test_local_cli_parser_loads_no_sqlalchemy():
    code = ("import contextlib, io\nfrom curation_workbench import cli\n"
            "with contextlib.redirect_stdout(io.StringIO()):\n"
            "    try:\n        cli.main(['--help'])\n    except SystemExit:\n        pass")
    assert [m for m in loaded_after(code) if not m.startswith("curation_workbench")] == []


def _imports(path):
    import ast
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom):
            yield ("." * node.level) + (node.module or "")


def test_only_hosted_imports_hosted_fastapi_or_snapgate():
    """Outside curation_workbench/hosted/, only cli.py names the hosted package
    (its lazy `from .hosted import cli`); nothing imports fastapi or snapgate."""
    from pathlib import Path

    import curation_workbench
    pkg = Path(curation_workbench.__file__).parent
    for path in sorted(pkg.rglob("*.py")):
        rel = path.relative_to(pkg)
        if rel.parts[0] == "hosted":
            continue
        for name in _imports(path):
            assert not name.startswith(("fastapi", "snapgate", "starlette", "uvicorn")), (rel, name)
            if "hosted" in name.split("."):
                assert rel.as_posix() == "cli.py", (rel, name)


def test_pull_needs_only_a_base_install():
    """`cwb pull` runs in a project's GitHub Action on a base install: no
    SQLAlchemy, FastAPI or snapgate."""
    loaded = loaded_after("from curation_workbench import pull, store\nfrom curation_workbench.store import landing")
    assert [m for m in loaded if m.startswith(("fastapi", "snapgate", "sqlalchemy", "psycopg"))] == []


def test_local_cli_help_loads_no_hosted_service_code():
    code = ("import contextlib, io\nfrom curation_workbench import cli\n"
            "with contextlib.redirect_stdout(io.StringIO()):\n"
            "    try:\n        cli.main(['hosted', '--help'])\n    except SystemExit:\n        pass")
    loaded = loaded_after(code)
    assert [m for m in loaded if m.startswith(("fastapi", "snapgate", "sqlalchemy"))] == []
    assert set(loaded) <= {"curation_workbench.hosted", "curation_workbench.hosted.cli"}


def test_hosted_site_never_loads_plugin_code():
    """The API, its services and the repo poller never load `workbench` (the
    only module that imports a project's plugins) or the apply engine."""
    code = ("import warnings\nwarnings.simplefilter('ignore')\n"
            "import curation_workbench.hosted.app, curation_workbench.hosted.cli\n"
            "import curation_workbench.hosted.sync.repos, curation_workbench.hosted.service.votes\n"
            "import sys\nprint('\\n'.join(m for m in sys.modules if m.startswith('curation_workbench')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = set(out.stdout.split())
    assert "curation_workbench.hosted.app" in loaded
    assert not loaded & {"curation_workbench.workbench", "curation_workbench.apply",
                         "curation_workbench.pull", "curation_workbench.handlers"}
