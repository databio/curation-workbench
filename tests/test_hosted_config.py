"""Hosted-mode config rules: every path stays in the checkout, plugins import
only from plugin_dir, and apply/routes/regen are ignored with warnings."""
import shutil
import sys
from pathlib import Path

import pytest

from curation_workbench import snapshot
from curation_workbench.config import ConfigError
from curation_workbench.workbench import PluginError, Workbench

TOY = Path(__file__).parent / "fixtures" / "toy"


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    return root


def configure(root, extra):
    cfg = root / "workbench.yaml"
    cfg.write_text(cfg.read_text() + extra)
    return cfg


def open_hosted(root, **kw):
    return Workbench.open(root / "workbench.yaml", root=root, hosted=True, **kw)


def test_plain_toy_opens_hosted(toy):
    wb = open_hosted(toy)
    assert wb.hosted and wb.warnings == []
    assert snapshot.build(wb)["warnings"] == []


@pytest.mark.parametrize("extra", [
    "curation: {dir: ../outside}\n",
    "review_types: ../../elsewhere.yaml\n",
    "mappings: {sssom: '../*.sssom.tsv'}\n",
    "plugin_dir: ../x\n",
    "out: ../out\n",
    "options: {machine_definitions: ../../defs.yaml}\n",
])
def test_paths_must_stay_inside(toy, extra):
    configure(toy, extra)
    with pytest.raises(ConfigError) as e:
        open_hosted(toy, plugins=False)
    assert e.value.code == "path_escape"


def test_root_is_forced_to_the_checkout(toy):
    configure(toy, "")
    (toy / "workbench.yaml").write_text((toy / "workbench.yaml").read_text().replace("root: .", "root: ../.."))
    wb = open_hosted(toy)
    assert wb.cfg.root == toy.resolve()


def test_symlink_out_fails(toy, tmp_path):
    (tmp_path / "secret.txt").write_text("x")
    (toy / "src" / "leak.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(ConfigError) as e:
        open_hosted(toy)
    assert e.value.code == "path_escape"


def test_symlink_inside_is_fine(toy):
    (toy / "notes.txt").symlink_to("src/things.yaml")
    open_hosted(toy)


def test_plugin_dir_required_when_plugins_listed(toy):
    configure(toy, "plugins: [p]\n")
    with pytest.raises(ConfigError) as e:
        open_hosted(toy)
    assert e.value.code == "config_invalid"
    open_hosted(toy, plugins=False)     # generic only: fine


PLUGIN = """import helper_mod

def register(wb):
    wb.routes.append(("GET", "/api/x", lambda api, tail: None))
    wb.regen.append(lambda wb, out: None)
    wb.signals["helped"] = lambda ctx: {s: helper_mod.ONE for s in ctx.subjects}
"""


def test_plugin_imports_only_from_plugin_dir(toy):
    (toy / "plug").mkdir()
    (toy / "plug" / "toy_hosted_plugin.py").write_text(PLUGIN)
    (toy / "plug" / "helper_mod.py").write_text("ONE = 1\n")
    configure(toy, "plugin_dir: plug\nplugins: [toy_hosted_plugin]\n"
                   "apply: {after_write: ['echo hi']}\n")
    pol = toy / "curation" / "policy.yaml"
    pol.write_text(pol.read_text().replace("queue:\n", "queue:\n  helped: 1.0\n"))
    before = list(sys.path)
    wb = open_hosted(toy)
    assert sys.path == before                       # plugin_dir removed again
    assert wb.routes == [] and wb.regen == []
    assert len(wb.warnings) == 3
    bundle = snapshot.build(wb)
    assert bundle["plugins"] == ["toy_hosted_plugin"]
    assert set(bundle["signals"]["helped"].values()) == {1}
    assert any("apply" in w for w in bundle["warnings"])


def test_plugin_name_must_be_a_file_in_plugin_dir(toy):
    (toy / "plug").mkdir()
    configure(toy, "plugin_dir: plug\nplugins: [json]\n")   # a stdlib module, not a file there
    with pytest.raises(PluginError):
        open_hosted(toy)


def test_plugin_import_error_is_structured(toy):
    (toy / "plug").mkdir()
    (toy / "plug" / "broken_plugin.py").write_text("import not_a_module_anywhere\n")
    configure(toy, "plugin_dir: plug\nplugins: [broken_plugin]\n")
    with pytest.raises(PluginError) as e:
        open_hosted(toy)
    assert e.value.code == "plugin_import_failed" and "not_a_module_anywhere" in str(e.value)


def test_local_mode_without_plugin_dir_unchanged(toy):
    (toy / "local_plugin_x.py").write_text("def register(wb):\n    wb.routes.append(1)\n")
    configure(toy, "plugins: [local_plugin_x]\n")
    wb = Workbench.open(toy / "workbench.yaml")
    assert wb.routes == [1]
