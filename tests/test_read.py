import unittest
from warehouse import Warehouse
from warehouse.samples import node_schema, node, tagged
from vendor.sdk1 import Resolver
from warehouse.errors import ResolutionError


class TestRead(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse()
        self.addCleanup(self.w.close)

    def read_rows(self, writer, reader, rows, batch="b"):
        wr = self.w.register("writer", writer)
        rr = self.w.register("reader", reader)
        self.w.append(batch, wr, rows)
        return self.w.run(self.w.create_read(rr, [batch]))

    def test_alias_default_and_promotion(self):
        writer = {"type": "record", "name": "old.Measure", "fields": [
            {"name": "n", "type": "int"}, {"name": "unused", "type": "string"}]}
        reader = {"type": "record", "name": "new.Measure", "aliases": ["old.Measure"],
                  "fields": [{"name": "count", "aliases": ["n"], "type": ["string", "long"]},
                             {"name": "unit", "type": "string", "default": "samples"}]}
        result = self.read_rows(writer, reader, [{"n": 7, "unused": "a"},
                                                 {"n": -2, "unused": "b"}])
        self.assertEqual(result["state"], "success")
        self.assertEqual(result["rows"], [
            {"count": tagged(1, 7), "unit": "samples"},
            {"count": tagged(1, -2), "unit": "samples"}])

    def test_unvisited_union_failure(self):
        rows = [node("root", tagged(0, 7), node("leaf", tagged(0, -4)))]
        result = self.read_rows(node_schema(), node_schema(True), rows)
        self.assertEqual(result["rows"], [{
            "label": "root", "value": tagged(1, 7),
            "next": tagged(1, {"label": "leaf", "value": tagged(1, -4), "next": tagged(0, None)})}])
        self.assertEqual(result["state"], "success")

    def test_enum_default_and_first_error(self):
        rows = [node("ok", tagged(0, 7)), node("outer", tagged(0, 1),
                node("bad", tagged(1, "B"))), node("later", tagged(1, "B"))]
        fallback = self.read_rows(node_schema(), node_schema(True, True), rows, "fallback")
        self.assertEqual(fallback["rows"][1]["next"]["value"]["value"], tagged(0, "UNKNOWN"))
        strict = self.read_rows(node_schema(), node_schema(True), rows, "strict")
        self.assertEqual(strict["state"], "failed")
        self.assertIsNone(strict["rows"])
        self.assertEqual(strict["error"], {
            "batch_id": "strict", "batch_index": 0, "row_index": 1,
            "code": "enum_symbol", "path": "$.next.value",
            "message": "reader has no symbol B"})
        self.assertEqual(self.w.db.connection.execute(
            "SELECT rows_json FROM published_results WHERE task_id=?",
            (strict["task_id"],)).fetchone()[0], None)

    def test_selected_branch_does_not_fall_back(self):
        writer = {"type": "record", "name": "R", "fields": [{"name": "x", "type": "int"}]}
        reader = [{"type": "record", "name": "A", "aliases": ["R"], "fields": [
            {"name": "x", "type": "string"}]},
            {"type": "record", "name": "B", "aliases": ["R"], "fields": [
                {"name": "x", "type": "long"}]}]
        result = self.read_rows(writer, reader, [{"x": 7}])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "incompatible")
        self.assertEqual(result["error"]["path"], "$.x")

    def test_enum_selected_branch_does_not_fall_back(self):
        writer = {"type": "enum", "name": "E", "symbols": ["A", "B"]}
        reader = [{"type": "enum", "name": "Strict", "aliases": ["E"], "symbols": ["A"]},
                  {"type": "enum", "name": "Loose", "aliases": ["E"], "symbols": ["A", "B"]}]
        result = self.read_rows(writer, reader, ["B"])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "enum_symbol")

    def test_arrays_missing_defaults_and_null(self):
        writer = {"type": "record", "name": "R", "fields": [
            {"name": "xs", "type": {"type": "array", "items": ["null", "int"]}}]}
        reader = {"type": "record", "name": "R", "fields": [
            {"name": "xs", "type": {"type": "array", "items": ["long", "null"]}},
            {"name": "extra", "type": {"type": "array", "items": "int"}, "default": []}]}
        result = self.read_rows(writer, reader, [
            {"xs": [tagged(0, None), tagged(1, 2)]}, {"xs": []}])
        self.assertEqual(result["rows"], [
            {"xs": [tagged(1, None), tagged(0, 2)], "extra": []},
            {"xs": [], "extra": []}])

    def test_incompatible_writer_branch_is_lazy(self):
        result = self.read_rows(["int", "string"], ["long"],
                                [tagged(0, 7), tagged(1, "x")])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["row_index"], 1)
        self.assertEqual(result["error"]["code"], "incompatible")
        self.assertIsNone(result["rows"])

    def test_pair_ambiguity_and_missing_field(self):
        writer = {"type": "record", "name": "R", "fields": [
            {"name": "old", "type": "int"}, {"name": "new", "type": "int"}]}
        reader = {"type": "record", "name": "R", "fields": [
            {"name": "new", "aliases": ["old"], "type": "int"}]}
        with self.assertRaises(ResolutionError) as caught:
            Resolver(writer, reader).read({"old": 1, "new": 2})
        self.assertEqual(caught.exception.code, "ambiguous_field")
        missing = {"type": "record", "name": "R", "fields": [{"name": "absent", "type": "int"}]}
        with self.assertRaises(ResolutionError) as caught:
            Resolver(writer, missing).read({"old": 1, "new": 2})
        self.assertEqual(caught.exception.code, "missing_field")

    def test_named_references_and_mutual_recursion(self):
        schema = {"type": "record", "name": "p.A", "fields": [
            {"name": "b", "type": ["null", {"type": "record", "name": "B", "fields": [
                {"name": "a", "type": ["null", "A"]}]}]}]}
        row = {"b": tagged(1, {"a": tagged(1, {"b": tagged(0, None)})})}
        self.assertEqual(Resolver(schema, schema).read(row), row)

    def test_long_promotion_is_exact(self):
        large = 2 ** 63 - 1
        self.assertEqual(Resolver("long", "long").read(large), large)
        self.assertEqual(Resolver("int", "long").read(-(2 ** 31)), -(2 ** 31))
