import pytest
import sqlite3
import os
import sys

# Add hippocampus dir to path so we can import modules directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture
def db():
    """Fresh in-memory DB with schema + migrations."""
    import schema
    conn = sqlite3.connect(":memory:")
    schema.init_db(conn)
    schema.migrate(conn)
    conn.commit()
    yield conn
    conn.close()
