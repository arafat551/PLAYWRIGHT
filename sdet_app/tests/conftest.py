import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TEST_DB = os.path.join(os.path.dirname(__file__), "test_qa.db")


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    from sdet_app import config
    config.env.DATABASE_PATH = TEST_DB

    import sdet_app.database as database
    for db_file in (TEST_DB, TEST_DB + "-wal", TEST_DB + "-shm"):
        if os.path.exists(db_file):
            os.remove(db_file)
    database.init_db(TEST_DB)
    yield database


@pytest.fixture(scope="session")
def client(_init_db):
    from sdet_app.app import app
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(scope="session")
def login(client):
    r = client.post("/login", data={"email": "admin@example.com", "password": "admin123"},
                    follow_redirects=True)
    assert r.status_code == 200
    return client
