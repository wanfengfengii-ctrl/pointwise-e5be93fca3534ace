import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from warehouse import Warehouse
from warehouse.samples import create_frozen


class TestPublish(unittest.TestCase):
    def test_failure_and_retry(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "warehouse.sqlite"
            w = Warehouse(path)
            revision = w.register("r", "int")
            w.append("b", revision, [1, 2, 2])
            task = w.create_read(revision, ["b"])
            def fail(conn):
                conn.execute("INSERT INTO read_receipts VALUES(?,?)", (task, "duplicate"))
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    w.run(task, before_commit=fail)
                self.assertEqual(w.result(task)["state"], "pending")
                for table in ["published_results", "read_receipts"]:
                    self.assertEqual(w.db.connection.execute(
                        "SELECT count(*) FROM " + table).fetchone()[0], 0)
            finally:
                w.close()
            with Warehouse(path) as reopened:
                result = reopened.run(task)
                self.assertEqual(result["rows"], [1, 2, 2])
                self.assertEqual(reopened.run(task), result)
                self.assertEqual(reopened.db.connection.execute(
                    "SELECT count(*) FROM read_receipts").fetchone()[0], 1)

    def test_failed_result_is_atomic_and_terminal(self):
        with Warehouse() as w:
            wr = w.register("w", ["int", "string"])
            rr = w.register("r", "long")
            w.append("a", wr, [{"branch": 0, "value": 7}])
            w.append("b", wr, [{"branch": 1, "value": "bad"}])
            task = w.create_read(rr, ["a", "b", "b"])
            def fail(conn):
                raise sqlite3.OperationalError("publication interrupted")
            with self.assertRaises(sqlite3.OperationalError):
                w.run(task, before_commit=fail)
            self.assertEqual(w.result(task)["state"], "pending")
            result = w.run(task)
            self.assertEqual(result["error"]["batch_index"], 1)
            self.assertEqual(result["error"]["row_index"], 0)
            self.assertIsNone(result["rows"])
            self.assertEqual(w.run(task), result)

    def test_frozen_database_reopen(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "frozen.sqlite"
            bindings = create_frozen(path)
            with Warehouse(path) as w:
                self.assertEqual(w.db.connection.execute(
                    "SELECT value FROM metadata WHERE key='version'").fetchone()[0], "1")
                self.assertEqual(w.result(bindings["tasks"]["success"])["state"], "success")
                self.assertEqual(w.result(bindings["tasks"]["failure"])["error"]["row_index"], 1)
                result = w.run(bindings["tasks"]["pending"])
                self.assertEqual(len(result["rows"]), 4)
                self.assertEqual(result["rows"][0], result["rows"][1])
                self.assertEqual(w.db.connection.execute("SELECT count(*) FROM read_receipts").fetchone()[0], 3)
