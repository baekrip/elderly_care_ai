from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.db import database as db_module
from server.db.database import create_tables, init_db
from server.db.models import ActivityFrameRecord  # noqa: F401


class DatabaseModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_tables_supports_sqlite_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "local.db"
            init_db(f"sqlite+aiosqlite:///{db_path.as_posix()}")
            await create_tables()
            if db_module.engine is not None:
                await db_module.engine.dispose()
            self.assertTrue(db_path.exists())

    async def test_sqlite_engine_applies_runtime_pragmas(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "local.db"
            init_db(f"sqlite+aiosqlite:///{db_path.as_posix()}")
            await create_tables()
            if db_module.engine is None:
                self.fail("Database engine is not initialized")
            async with db_module.engine.connect() as connection:
                journal_mode = (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar()
                busy_timeout = (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar()
            await db_module.engine.dispose()

            self.assertEqual(str(journal_mode).lower(), "wal")
            self.assertEqual(int(busy_timeout), 30000)


if __name__ == "__main__":
    unittest.main()
