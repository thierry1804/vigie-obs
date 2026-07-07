# ruff: noqa: E402
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="vigie-test-")
os.environ["VIGIE_DATA_DIR"] = _tmp
os.environ["VIGIE_DB_PATH"] = os.path.join(_tmp, "vigie.db")
os.environ["VIGIE_MOCK_LLM"] = "1"
os.environ["VIGIE_API_TOKEN"] = ""

import pytest

from agent.db.session import init_db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db = tmp_path / "vigie.db"
    os.environ["VIGIE_DB_PATH"] = str(db)
    os.environ["VIGIE_DATA_DIR"] = str(tmp_path)
    init_db(str(db))
    yield
