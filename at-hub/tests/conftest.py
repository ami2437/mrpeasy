"""Test harness: the real AT-HUB app on a throwaway database and upload folder.

Run from at-hub/:   venv\\Scripts\\python -m pytest            (fast suite)
                    venv\\Scripts\\python -m pytest -m import  (also re-runs the MRPeasy import + reconcile)

Nothing here touches at_hub.db, uploads/ or any outside service: AI and email are switched off
and the database / uploads live in a temp folder that is deleted afterwards.
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="athub-test-"))
# must be set before the app is imported: settings + database are read at import time
os.environ.update({
    "DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    "UPLOAD_DIR": str(_TMP / "uploads"),
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "test-admin-pass",
    "SECRET_KEY": "test-secret-key-not-for-production",
    "ANTHROPIC_API_KEY": "",            # no cloud AI
    "AI_OLLAMA_URL": "http://127.0.0.1:9",  # no local AI
    "SMTP_HOST": "",                    # no email
    "TEST_DATA_ENABLED": "false",
    "BACKUP_DIR": str(_TMP / "backups"),
    "BACKUP_EVERY_HOURS": "0",          # no background backups in tests
})

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.services.auth import AuthService  # noqa: E402
from tests.builders import Builders  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "import_: re-runs the MRPeasy import against the latest snapshot (slow)")


def pytest_collection_modifyitems(config, items):
    if "import_" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="slow import test -- run with: pytest -m import_")
    for item in items:
        if "import_" in item.keywords:
            item.add_marker(skip)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_headers():
    return {"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'admin'})}"}


@pytest.fixture
def api(client, admin_headers):
    """client calls as admin; raises with the server's message on any unexpected status."""
    class Api:
        def call(self, method, url, expect=(200, 201, 204), **kw):
            r = client.request(method, url, headers=admin_headers, **kw)
            if expect and r.status_code not in (expect if isinstance(expect, tuple) else (expect,)):
                raise AssertionError(f"{method} {url} -> {r.status_code}: {r.text[:400]}")
            return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r
        get = lambda self, url, **kw: self.call("GET", url, **kw)
        post = lambda self, url, **kw: self.call("POST", url, **kw)
        put = lambda self, url, **kw: self.call("PUT", url, **kw)
        delete = lambda self, url, **kw: self.call("DELETE", url, **kw)
    return Api()


@pytest.fixture
def make(api):
    return Builders(api)
