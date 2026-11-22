import pytest
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.main import app


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_db_not_configured(client):
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, database_url=None)
    resp = client.get("/health/db")
    assert resp.status_code == 503
    assert resp.json() == {"status": "error", "database": "not_configured"}


def test_health_db_unreachable_does_not_leak_credentials(client):
    # Port 1 on localhost: connection is refused immediately.
    url = "postgresql://someuser:s3cret-pw@127.0.0.1:1/postgres"
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, database_runtime_url=SecretStr(url), db_connect_timeout=2
    )
    resp = client.get("/health/db")
    assert resp.status_code == 503
    assert resp.json() == {"status": "error", "database": "unreachable"}
    assert "s3cret-pw" not in resp.text


@pytest.mark.integration
def test_health_db_ok_against_local_postgres(client):
    if get_settings().database_url is None:
        pytest.skip("DATABASE_URL not configured; see backend/.env.example")
    resp = client.get("/health/db")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok", "database": "ok"}
