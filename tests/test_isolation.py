"""`cwb hosted isolation-check` logic, without the network: the probe project
is a valid project, and the verdict follows the self-check and probe results.
(The real check runs in the deployed worker task against curate-runner.)"""
from curation_workbench import runner
from curation_workbench.hosted.sync import isolation
from curation_workbench.hosted.sync.remote import LocalRunner


class Fake:
    def __init__(self, isolated, probe):
        self.isolated, self.probe_ = isolated, probe

    def get_json(self, path, timeout=60):
        return {"isolated": self.isolated, "checks": [{"name": "x", "ok": self.isolated, "detail": ""}]}

    def snapshot(self, members, config_path, plugins_enabled):
        assert plugins_enabled and config_path == "workbench.yaml"
        return {"warnings": [f"PROBE {k}={v}" for k, v in self.probe_.items()]}


BLOCKED = {"secret": "blocked (FileNotFoundError)", "network": "blocked (OSError)", "write": "blocked"}


def test_probe_project_is_valid_without_plugins():
    bundle = LocalRunner(run_fn=runner.in_process_runner).snapshot(
        isolation.probe_members(), "workbench.yaml", False)
    assert bundle["subjects"]["counts"]["class"] == 1


def test_verdicts():
    lines = []
    assert isolation.run_isolation_check("http://r", runner=Fake(True, BLOCKED), out=lines.append) == 0
    assert lines[-1] == "ISOLATED"
    for leak in ({"secret": "READ"}, {"network": "CONNECTED"}, {"write": "WROTE /"}):
        lines = []
        assert isolation.run_isolation_check("http://r", runner=Fake(True, {**BLOCKED, **leak}),
                                             out=lines.append) == 1
        assert lines[-1] == "NOT ISOLATED"
    assert isolation.run_isolation_check("http://r", runner=Fake(False, BLOCKED), out=lines.append) == 1
    lines = []
    assert isolation.run_isolation_check(None, out=lines.append) == 1 and "CWB_RUNNER_URL" in lines[0]
