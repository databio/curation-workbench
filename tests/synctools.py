"""Helpers for the sync tests: a throwaway git repo holding the toy project
plus a small plugin, served over file:// (never GitHub)."""
import os
import shutil
import subprocess
from pathlib import Path

TOY = Path(__file__).parent / "fixtures" / "toy"

PLUGIN = '''"""Toy plugin: one finding and one decision check."""


def findings(wb, fingerprint, severity_of):
    return ([{"id": "plug:color", "rule": "plugin-rule", "severity": "info",
              "message": "%(message)s", "subjects": ["slot:color"]}],
            [{"path": "plugin", "findings": 1, "stale": False}])


def no_blue(wb, rec):
    return ["the toy plugin refuses decisions about blue"] if rec["subject"] == "term:TOY_0002" else []


def register(wb):
    wb.findings_sources.append(findings)
    wb.record_checks.append(no_blue)
'''

GIT_ENV = {"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.org",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.org",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


def git(repo, *args):
    env = {**os.environ, **GIT_ENV}
    out = subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def make_repo(path, plugin_message="plugin finding"):
    """A git repo (branch master) with the toy project, `plugin_dir: plugin`
    and `plugins: [toy_plugin]`. Returns its path."""
    path = Path(path)
    shutil.copytree(TOY, path)
    cfg = (path / "workbench.yaml").read_text()
    (path / "workbench.yaml").write_text(cfg + "plugin_dir: plugin\nplugins: [toy_plugin]\n")
    (path / "plugin").mkdir()
    (path / "plugin" / "toy_plugin.py").write_text(PLUGIN % {"message": plugin_message})
    git(path, "init", "--quiet", "-b", "master")
    git(path, "add", "-A")
    git(path, "commit", "--quiet", "-m", "toy")
    return path


def commit(path, message="change"):
    git(path, "add", "-A")
    git(path, "commit", "--quiet", "-m", message)
    return git(path, "rev-parse", "HEAD")


def set_plugin_message(path, message):
    (Path(path) / "plugin" / "toy_plugin.py").write_text(PLUGIN % {"message": message})
    return commit(path, "plugin change")


def edit_description(path, old, new):
    """Edit text in the toy's things.yaml (changes subject hashes) and commit."""
    f = Path(path) / "src" / "things.yaml"
    text = f.read_text()
    assert old in text
    f.write_text(text.replace(old, new))
    return commit(path, "edit")
