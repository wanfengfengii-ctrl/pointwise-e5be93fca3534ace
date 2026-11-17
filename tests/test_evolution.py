import unittest
from warehouse import Warehouse, NotFound


class TestEvolution(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse()
        self.addCleanup(self.w.close)

    def test_fixed_task_revision(self):
        writer = self.w.register("write", "int")
        old = self.w.register("read", ["string", "long"])
        self.w.append("a", writer, [7, 7])
        task = self.w.create_read(old, ["a", "a"])
        new = self.w.register("read", ["long", "string"])
        self.w.append("b", writer, [-2])
        recent = self.w.create_read(new, ["b", "a"])
        first = self.w.run(task)
        self.assertEqual(first["rows"], [{"branch": 1, "value": 7}] * 4)
        self.assertEqual(self.w.run(recent)["rows"], [
            {"branch": 0, "value": -2}, {"branch": 0, "value": 7}, {"branch": 0, "value": 7}])
        self.assertEqual(self.w.schemas.get(old), ["string", "long"])
        self.assertEqual(self.w.schemas.head("read"), new)
        self.assertEqual(self.w.run(task), first)
        self.assertEqual(self.w.db.connection.execute("SELECT count(*) FROM read_receipts").fetchone()[0], 2)

    def test_missing_binding_and_pending(self):
        revision = self.w.register("r", "int")
        with self.assertRaises(NotFound):
            self.w.create_read(revision, ["missing"])
        with self.assertRaises(NotFound):
            self.w.create_read(999, [])
        self.assertEqual(self.w.db.connection.execute("SELECT count(*) FROM read_tasks").fetchone()[0], 0)
        task = self.w.create_read(revision, [])
        self.assertEqual(self.w.result(task), {
            "task_id": task, "state": "pending", "rows": None, "error": None, "receipt": None})
        self.assertEqual(self.w.run(task)["rows"], [])
        with self.assertRaises(NotFound):
            self.w.result(999)
