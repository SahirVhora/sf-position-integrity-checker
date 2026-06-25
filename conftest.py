"""pytest session fixture: initialise a temp SQLite DB before schema tests run."""

import pytest
import database as db


@pytest.fixture(autouse=True, scope="session")
def tmp_db(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    db.DB_PATH = str(data_dir / "sf_integrity_TEST.db")
    db.init_db()
    yield
