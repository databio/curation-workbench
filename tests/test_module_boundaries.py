"""The isolated runner loads no hosted, database or snapgate code, and the
local CLI loads no SQLAlchemy."""
import subprocess
import sys

FORBIDDEN = ("sqlalchemy", "psycopg", "snapgate", "fastapi", "curation_workbench.hosted")


def loaded_after(code):
    probe = (f"import sys\n{code}\n"
             f"print('\\n'.join(m for m in sys.modules if m.startswith({FORBIDDEN!r})))")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    return [m for m in out.stdout.split() if m]


def test_runner_imports_no_hosted_code():
    assert loaded_after("import curation_workbench.runner as r; r.self_check") == []


def test_runner_job_path_imports_no_hosted_code(tmp_path):
    code = ("from curation_workbench import runner, snapshot, derive, workbench, config, store\n"
            "runner.classify(ValueError('x'))")
    assert loaded_after(code) == []


def test_runner_source_names_no_hosted_module():
    import ast

    import curation_workbench.runner as r
    tree = ast.parse(open(r.__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [("." * node.level) + (node.module or "")]
        else:
            continue
        for n in names:
            assert not n.startswith(FORBIDDEN) and "hosted" not in n, n


def test_local_cli_parser_loads_no_sqlalchemy():
    code = ("import contextlib, io\nfrom curation_workbench import cli\n"
            "with contextlib.redirect_stdout(io.StringIO()):\n"
            "    try:\n        cli.main(['--help'])\n    except SystemExit:\n        pass")
    assert [m for m in loaded_after(code) if not m.startswith("curation_workbench")] == []
