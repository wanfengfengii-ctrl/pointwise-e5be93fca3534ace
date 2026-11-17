import json
import sqlite3
import unittest
from importlib.resources import files
from vendor.sdk2 import Program, ProgramError, ExecutionError
from warehouse.samples import recursive_program, node, tagged


class TestSDK2(unittest.TestCase):
    def test_recursive_native_program_and_serialization(self):
        document = recursive_program()
        program = Program(json.loads(json.dumps(document)))
        row = node("root", tagged(0, 7), node("leaf", tagged(1, "B")))
        expected = {"label": "root", "next": tagged(1, {
            "label": "leaf", "next": tagged(0, None)})}
        self.assertEqual(program.execute(row), expected)
        self.assertEqual(Program(program.to_json()).execute(row), expected)
        document["nodes"]["copy"]["op"] = "fail"
        self.assertEqual(program.execute(row), expected)

    def test_lazy_switch_reorder_enum_and_exact_integer(self):
        program = Program({"entry": "switch", "nodes": {
            "switch": {"op": "switch", "branches": ["number", "symbol"]},
            "number": {"op": "wrap", "branch": 1, "next": "integer"},
            "integer": {"op": "check", "kind": "long", "next": "copy"},
            "copy": {"op": "copy"},
            "symbol": {"op": "wrap", "branch": 0, "next": "enum"},
            "enum": {"op": "enum", "mapping": {"A": "A"}}}})
        self.assertEqual(program.execute(tagged(0, 2 ** 63 - 1)), tagged(1, 2 ** 63 - 1))
        self.assertEqual(program.execute(tagged(1, "A")), tagged(0, "A"))
        with self.assertRaises(ExecutionError) as caught:
            program.execute(tagged(1, "B"))
        self.assertEqual((caught.exception.code, caught.exception.path), ("enum_symbol", "$"))
        with self.assertRaises(ExecutionError):
            program.execute(tagged(0, True))
        default = program.to_json()
        default["nodes"]["enum"]["default"] = "UNKNOWN"
        self.assertEqual(Program(default).execute(tagged(1, "B")), tagged(0, "UNKNOWN"))

    def test_array_record_constants_and_explicit_failure(self):
        document = {"entry": "array", "nodes": {
            "array": {"op": "array", "item": "record"},
            "record": {"op": "record", "fields": [
                {"name": "renamed", "source": "old", "next": "copy"},
                {"name": "unit", "source": None, "next": "constant"}]},
            "copy": {"op": "copy"}, "constant": {"op": "const", "value": []}}}
        result = Program(document).execute([{"old": 7}, {"old": 7}])
        self.assertEqual(result, [{"renamed": 7, "unit": []}, {"renamed": 7, "unit": []}])
        result[0]["unit"].append(1)
        self.assertEqual(result[1]["unit"], [])
        with self.assertRaises(ExecutionError) as caught:
            Program(document).execute([{"old": 7}, {}])
        self.assertEqual(caught.exception.path, "$[1].renamed")
        document["nodes"]["constant"] = {"op": "fail", "code": "missing_field", "message": "no default"}
        with self.assertRaises(ExecutionError) as caught:
            Program(document).execute([{"old": 7}])
        self.assertEqual(caught.exception.path, "$[0].unit")

    def test_program_validation_and_checks(self):
        with self.assertRaises(ProgramError):
            Program({"entry": "x", "nodes": {"x": {"op": "call", "target": "missing"}}})
        with self.assertRaises(ProgramError):
            Program({"entry": "x", "nodes": {"x": {"op": "schema_resolve"}}})
        p = Program({"entry": "r", "nodes": {
            "r": {"op": "check", "kind": "record", "keys": ["x"], "next": "copy"},
            "copy": {"op": "copy"}}})
        self.assertEqual(p.execute({"x": None}), {"x": None})
        with self.assertRaises(ExecutionError):
            p.execute({"x": None, "extra": 1})

    def test_target_schema_is_empty_and_constrained(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(files("warehouse").joinpath("schema_v2.sql").read_text())
        for table in ["v2_revisions", "v2_heads", "v2_programs", "v2_bindings",
                      "v2_batches", "v2_tasks", "v2_results", "v2_receipts"]:
            self.assertEqual(conn.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO v2_revisions VALUES(1,'bad','missing')")
