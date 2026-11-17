import unittest
from warehouse import Warehouse, SchemaError, PayloadError, Conflict, NotFound
from warehouse.samples import node_schema, node, tagged


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse()
        self.addCleanup(self.w.close)

    def test_recursive_batch(self):
        revision = self.w.register("observations", node_schema())
        row = node("root", tagged(0, 7), node("leaf", tagged(1, "A")))
        expected = [row, row, node("end", tagged(0, 0))]
        receipt = self.w.append("b", revision, expected)
        row["name"] = "changed outside archive"
        stored = self.w.batches.get("b")
        self.assertEqual(stored["rows"][0]["name"], "root")
        self.assertEqual(stored["rows"][0], stored["rows"][1])
        self.assertEqual(stored["rows"][2]["next"], tagged(0, None))
        self.assertEqual(stored["writer_revision"], revision)
        self.assertEqual(self.w.append("b", revision, stored["rows"]), receipt)
        stored["rows"][0]["name"] = "changed returned object"
        self.assertEqual(self.w.batches.get("b")["rows"][0]["name"], "root")

    def test_ambiguous_alias(self):
        old = self.w.register("reading", "int")
        bad = {"type": "record", "name": "R", "fields": [
            {"name": "a", "aliases": ["old"], "type": "int"},
            {"name": "b", "aliases": ["old"], "type": "int"}]}
        with self.assertRaises(SchemaError):
            self.w.register("reading", bad)
        self.assertEqual(self.w.schemas.head("reading"), old)
        self.assertEqual(self.w.db.connection.execute(
            "SELECT count(*) FROM schema_revisions").fetchone()[0], 1)

    def test_invalid_union_tag(self):
        revision = self.w.register("u", ["int", "string"])
        for value in [tagged(8, 1), tagged(True, 1), tagged(0, "wrong")]:
            with self.assertRaises(PayloadError):
                self.w.append("broken", revision, [tagged(0, 7), value])
            with self.assertRaises(NotFound):
                self.w.batches.get("broken")
        self.assertEqual(self.w.db.connection.execute("SELECT count(*) FROM batches").fetchone()[0], 0)

    def test_conflicts_and_bounds(self):
        revision = self.w.register("i", "int")
        self.w.append("b", revision, [0, 0])
        with self.assertRaises(Conflict):
            self.w.append("b", revision, [0])
        for invalid in [True, 2 ** 31, -(2 ** 31) - 1, 1.0, None]:
            with self.assertRaises(PayloadError):
                self.w.append("bad", revision, [invalid])
        long = self.w.register("l", "long")
        self.w.append("large", long, [2 ** 63 - 1, -(2 ** 63)])

    def test_schema_references_defaults_and_names(self):
        for schema in ["Missing", [], ["int", "int"],
                       {"type": "record", "name": "R", "fields": [
                           {"name": "x", "type": "int", "default": "bad"}]},
                       {"type": "enum", "name": "E", "symbols": ["A"], "default": "B"}]:
            with self.assertRaises(SchemaError):
                self.w.register("bad", schema)
        schema = {"type": "record", "name": "root.R", "fields": [
            {"name": "child", "type": ["null", "root.R"], "default": tagged(0, None)}]}
        self.w.register("valid", schema)

    def test_append_missing_revision_and_empty_batch(self):
        with self.assertRaises(NotFound):
            self.w.append("b", 99, [])
        revision = self.w.register("i", "int")
        self.w.append("empty", revision, [])
        self.assertEqual(self.w.batches.get("empty")["rows"], [])
