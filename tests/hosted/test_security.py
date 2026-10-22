"""A production boot refuses dev flags, test secrets and inconsistent URLs."""
import pytest

from curation_workbench.hosted.config import Settings
from curation_workbench.hosted.security import (InsecureConfigError, production_violations,
                                                validate_production_security)
from snapgate import settings as sg_settings

pytestmark = pytest.mark.postgres

PUBLIC = "https://curate.databio.org"


@pytest.fixture
def prod(monkeypatch):
    """Snapgate settings a production deploy would have."""
    for k, v in {"JWT_SECRET_KEY": "j" * 40, "SESSION_SECRET_KEY": "s" * 40,
                 "OIDC_ISSUER": "https://prosopia.example", "OIDC_CLIENT_ID": "curate",
                 "OIDC_CLIENT_SECRET": "c" * 30, "OIDC_REDIRECT_URI": PUBLIC + "/auth/login/callback",
                 "FRONTEND_URL": PUBLIC, "DEBUG": False, "DEMO_LOGIN": False,
                 "SNAPGATE_SUPERADMIN_EMAILS": "boss@example.org"}.items():
        monkeypatch.setattr(sg_settings, k, v)
    return Settings(env="production", public_url=PUBLIC)


def test_good_production_config_boots(prod):
    assert production_violations(sg_settings, prod) == []
    validate_production_security(sg_settings, prod)


@pytest.mark.parametrize("field, value, needle", [
    ("DEMO_LOGIN", True, "DEMO_LOGIN"),
    ("DEBUG", True, "DEBUG"),
    ("SNAPGATE_SUPERADMIN_EMAILS", "", "SNAPGATE_SUPERADMIN_EMAILS"),
    ("OIDC_REDIRECT_URI", "https://evil.example/auth/login/callback", "OIDC_REDIRECT_URI"),
    ("FRONTEND_URL", "http://127.0.0.1:5190", "FRONTEND_URL"),
])
def test_refusals(prod, monkeypatch, field, value, needle):
    monkeypatch.setattr(sg_settings, field, value)
    with pytest.raises(InsecureConfigError, match=needle):
        validate_production_security(sg_settings, prod)


def test_test_secrets_refused(prod, monkeypatch):
    default = type(sg_settings).model_fields["JWT_SECRET_KEY"].default
    monkeypatch.setattr(sg_settings, "JWT_SECRET_KEY", default)
    assert any("JWT_SECRET_KEY" in v for v in production_violations(sg_settings, prod))


def test_http_public_url_refused(prod, monkeypatch):
    s = Settings(env="production", public_url="http://curate.databio.org")
    monkeypatch.setattr(sg_settings, "OIDC_REDIRECT_URI", "http://curate.databio.org/auth/login/callback")
    monkeypatch.setattr(sg_settings, "FRONTEND_URL", "http://curate.databio.org")
    assert any("must be https" in v for v in production_violations(sg_settings, s))


def test_unset_env_is_production(monkeypatch):
    monkeypatch.delenv("CWB_ENV", raising=False)
    assert Settings().env == "production"


def test_dev_allows_everything(monkeypatch):
    monkeypatch.setattr(sg_settings, "DEMO_LOGIN", True)
    assert production_violations(sg_settings, Settings(env="dev")) == []


def test_dev_snapshot_is_dev_only(monkeypatch):
    from curation_workbench import cli
    from curation_workbench.hosted.config import get_settings
    for env in ("production", None):
        if env:
            monkeypatch.setenv("CWB_ENV", env)
        else:
            monkeypatch.delenv("CWB_ENV")
        get_settings.cache_clear()
        with pytest.raises(SystemExit, match="dev only"):
            cli.main(["hosted", "admin", "dev-snapshot", "--project", "toy", "--snapshot", "x.json"])
