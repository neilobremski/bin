import pytest
import sqlite3
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


@pytest.fixture
def db():
    """Fresh in-memory DB with schema."""
    import schema
    conn = sqlite3.connect(":memory:")
    schema.init_db(conn)
    conn.commit()
    yield conn
    conn.close()
