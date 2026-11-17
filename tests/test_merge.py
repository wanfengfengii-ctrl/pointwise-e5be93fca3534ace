"""Independent confluence accounting and union regressions (MERGING.md)."""
import unittest
from copy import deepcopy

from vendor.sdk1 import Schema
from warehouse import (Warehouse, Conflict, MergeConflict, NotFound,
                       PayloadError, SchemaError)


def tag(branch, value):
    return {"branch": branch, "value": value}


def normalize(raw):
    """Canonical form of one parsed schema, ignoring declaration placement."""
    schema = Schema(raw)

    def ref(node):
        if node.name:
            return ("named", node.kind, node.name)
        if node.kind == "union":
            return ("union", tuple(ref(x) for x in node.branches))
        if node.kind == "array":
            return ("array", ref(node.items))
        return (node.kind,)

    definitions = []
    for name, node in sorted(schema.names.items()):
        if node.kind == "record":
            body = tuple((f, ref(t), tuple(sorted(a)), present,
                          d if present else None)
                         for f, t, a, present, d in node.fields)
        else:
            body = (node.symbols, node.enum_default)
        definitions.append((name, node.kind, tuple(sorted(node.aliases)), body))
    return (ref(schema.root), tuple(definitions))


def node_base():
    return {"type": "record", "name": "research.Node", "fields": [
        {"name": "id", "type": "int"},
        {"name": "next", "type": ["null", "research.Node"], "default": tag(0, None)}]}


def node_left():
    schema = node_base()
    schema["fields"][0].update(name="sample", aliases=["id"])
    return schema


def node_right():
    schema = node_base()
    schema["fields"][0]["type"] = "long"
    schema["fields"][1].update(type=["research.Node", "null"], default=tag(1, None))
    return schema


def node_expected():
    schema = node_base()
    schema["fields"][0].update(name="sample", aliases=["id"], type="long")
    schema["fields"][1].update(type=["research.Node", "null"], default=tag(1, None))
    return schema


def envelope(node_schema, row):
    return {"type": "record", "name": "Envelope", "fields": [
        {"name": "prototype", "type": node_schema, "default": deepcopy(row)},
        {"name": "copies", "type": {"type": "array", "items": "research.Node"},
         "default": [deepcopy(row), deepcopy(row)]}]}


def mutual_case():
    base = {"type": "record", "name": "A", "fields": [
        {"name": "first", "type": {"type": "record", "name": "N", "fields": [
            {"name": "v", "type": "int"},
            {"name": "peer", "type": ["null", {"type": "record", "name": "B",
                                                "fields": [
                {"name": "back", "type": ["null", "N"]},
                {"name": "flag", "type": "int"}]}]}]}},
        {"name": "second", "type": "N"}]}
    left, right, expected = (deepcopy(base) for _ in range(3))
    for schema in (left, expected):
        schema["fields"][0]["type"]["fields"][0].update(name="value", aliases=["v"])
    for schema in (right, expected):
        schema["fields"][0]["type"]["fields"][1]["type"][1]["fields"][1]["type"] = "long"
    return base, left, right, expected


def conflict_trios():
    base, right = node_base(), node_right()
    left = node_base()
    del left["fields"][0]
    yield "delete-edit", base, left, right
    base = {"type": "record", "name": "R", "fields": [
        {"name": "a", "type": "int"}, {"name": "b", "type": "int"}]}
    left = {"type": "record", "name": "R", "fields": [
        {"name": "x", "aliases": ["a", "b"], "type": "int"}]}
    yield "identity", base, left, deepcopy(base)
    base = {"type": "record", "name": "R", "fields": [
        {"name": "x", "type": ["int", "string"]}]}
    left, right = deepcopy(base), deepcopy(base)
    left["fields"][0]["type"] = ["int"]
    right["fields"][0]["default"] = tag(1, "kept")
    yield "unrepresentable-default", base, left, right
    base, left, right = node_base(), node_base(), node_base()
    left["fields"][0].update(name="l", aliases=["id"])
    right["fields"][0].update(name="r", aliases=["id"])
    yield "rename-rename", base, left, right
    base = {"type": "record", "name": "R", "fields": [{"name": "x", "type": "int"}]}
    left = {"type": "record", "name": "R", "fields": [
        {"name": "x", "type": {"type": "record", "name": "S", "fields": []}}]}
    yield "definition-set", base, left, deepcopy(base)
    base = {"type": "enum", "name": "E", "symbols": ["A", "B"]}
    left = {"type": "enum", "name": "E", "symbols": ["A"]}
    right = {"type": "enum", "name": "E", "symbols": ["B"]}
    yield "enum-symbols", base, left, right
    base = {"type": "enum", "name": "E", "symbols": ["A", "B"], "default": "B"}
    left = {"type": "enum", "name": "E", "symbols": ["A"]}
    right = {"type": "enum", "name": "E", "symbols": ["A", "B"], "default": "A"}
    yield "enum-default", base, left, right
    base = {"type": "record", "name": "R", "fields": [
        {"name": "x", "type": [{"type": "array", "items": "int"}, "string"]}]}
    left, right = deepcopy(base), deepcopy(base)
    left["fields"][0]["type"] = ["string"]
    right["fields"][0]["type"][0]["items"] = "long"
    yield "branch-delete-modify", base, left, right
    base = {"type": "record", "name": "R", "fields": [
        {"name": "a", "type": "int"}, {"name": "b", "type": "int"}]}
    left, right = deepcopy(base), deepcopy(base)
    left["fields"][0].update(name="x", aliases=["a"])
    right["fields"][1].update(name="x", aliases=["b"])
    yield "merged-name-collision", base, left, right


class TestMerge(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse()
        self.addCleanup(self.w.close)

    def snapshot(self):
        tables = ("schema_revisions", "schema_heads", "read_tasks",
                  "published_results", "read_receipts")
        return {t: list(map(tuple, self.w.db.connection.execute(
            "SELECT * FROM " + t))) for t in tables}

    def register_all(self, *schemas):
        return [self.w.register("case-" + str(i), s)
                for i, s in enumerate(schemas)]

    def test_recursive_rename_promote_reorder_and_reads(self):
        revisions = self.register_all(node_base(), node_left(), node_right())
        before = self.snapshot()
        merged = self.w.merge_schema(*revisions)
        self.assertEqual(normalize(merged), normalize(node_expected()))
        self.assertEqual(self.snapshot(), before)
        again = self.w.merge_schema(*revisions)
        self.assertEqual(again, merged)
        again["fields"][0]["name"] = "mutated outside"
        self.assertEqual(self.w.merge_schema(*revisions), merged)
        left_row = {"sample": 7,
                    "next": tag(1, {"sample": -2, "next": tag(0, None)})}
        right_row = {"id": 2 ** 40, "next": tag(1, None)}
        self.w.append("left", revisions[1], [left_row])
        self.w.append("right", revisions[2], [right_row])
        old = self.w.create_read(revisions[1], ["left"])
        created = self.w.create_merged_read("combined", *revisions,
                                            ["left", "right", "left"])
        self.assertEqual(self.w.schemas.head("combined"), created["revision"])
        self.assertEqual(normalize(self.w.schemas.get(created["revision"])),
                         normalize(node_expected()))
        task = self.w.tasks.get(created["task_id"])
        self.assertEqual(task["reader_revision"], created["revision"])
        self.assertEqual(task["batch_ids"], ["left", "right", "left"])
        rows = [{"sample": 7, "next": tag(0, {"sample": -2, "next": tag(1, None)})},
                {"sample": 2 ** 40, "next": tag(1, None)},
                {"sample": 7, "next": tag(0, {"sample": -2, "next": tag(1, None)})}]
        result = self.w.run(created["task_id"])
        self.assertEqual(result["state"], "success")
        self.assertEqual(result["rows"], rows)
        self.assertEqual(self.w.run(created["task_id"]), result)
        self.w.register("combined", "string")
        self.assertEqual(self.w.tasks.get(old)["reader_revision"], revisions[1])
        self.assertEqual(self.w.run(old)["rows"], [left_row])

    def test_union_branch_identity_default(self):
        base = {"type": "record", "name": "R", "fields": [
            {"name": "x", "type": ["int", "long"], "default": tag(0, 7)}]}
        left, right, expected = (deepcopy(base) for _ in range(3))
        left["fields"][0].update(type=["long", "int"], default=tag(1, 7))
        right["fields"][0]["default"] = tag(0, 9)
        expected["fields"][0].update(type=["long", "int"], default=tag(1, 9))
        revisions = self.register_all(base, left, right)
        merged = self.w.merge_schema(*revisions)
        self.assertEqual(normalize(merged), normalize(expected))
        self.assertEqual(merged["fields"][0]["default"], tag(1, 9))
        self.w.append("left", revisions[1], [{"x": tag(1, 5)}])
        self.w.append("right", revisions[2], [{"x": tag(0, 9)}])
        created = self.w.create_merged_read("u", *revisions, ["left", "right"])
        # Runtime reads keep the reader's first-compatible-branch rule; only
        # defaults are re-encoded by branch identity.
        self.assertEqual(self.w.run(created["task_id"])["rows"],
                         [{"x": tag(0, 5)}, {"x": tag(0, 9)}])

    def test_nested_record_and_array_defaults(self):
        rows = [{"id": 7, "next": tag(0, None)},
                {"sample": 7, "next": tag(0, None)},
                {"id": 9, "next": tag(1, None)},
                {"sample": 9, "next": tag(1, None)}]
        schemas = [envelope(node_base(), rows[0]), envelope(node_left(), rows[1]),
                   envelope(node_right(), rows[2])]
        expected = envelope(node_expected(), rows[3])
        revisions = self.register_all(*schemas)
        self.assertEqual(normalize(self.w.merge_schema(*revisions)),
                         normalize(expected))

    def test_mutual_recursion_and_shared_definition(self):
        base, left, right, expected = mutual_case()
        revisions = self.register_all(base, left, right)
        merged = self.w.merge_schema(*revisions)
        self.assertEqual(normalize(merged), normalize(expected))
        self.assertEqual(sorted(Schema(merged).names), ["A", "B", "N"])
        left_row = {"first": {"value": 1, "peer": tag(1, {
                        "back": tag(0, None), "flag": 3})},
                    "second": {"value": 2, "peer": tag(0, None)}}
        right_row = {"first": {"v": 4, "peer": tag(0, None)},
                     "second": {"v": 5, "peer": tag(1, {
                         "back": tag(0, None), "flag": 2 ** 40})}}
        self.w.append("left", revisions[1], [left_row])
        self.w.append("right", revisions[2], [right_row])
        created = self.w.create_merged_read("m", *revisions, ["left", "right"])
        self.assertEqual(self.w.run(created["task_id"])["rows"], [
            {"first": {"value": 1, "peer": tag(1, {
                "back": tag(0, None), "flag": 3})},
             "second": {"value": 2, "peer": tag(0, None)}},
            {"first": {"value": 4, "peer": tag(0, None)},
             "second": {"value": 5, "peer": tag(1, {
                 "back": tag(0, None), "flag": 2 ** 40})}}])

    def test_field_additions_and_deletions(self):
        base = {"type": "record", "name": "R", "fields": [
            {"name": "keep", "type": "int"}, {"name": "drop", "type": "int"}]}
        left, right = deepcopy(base), deepcopy(base)
        del left["fields"][1]
        right["fields"].append({"name": "extra", "type": "string",
                                "default": "x"})
        revisions = self.register_all(base, left, right)
        merged = self.w.merge_schema(*revisions)
        self.assertEqual([f["name"] for f in merged["fields"]],
                         ["keep", "extra"])
        self.assertEqual(merged["fields"][1]["default"], "x")
        both = deepcopy(base)
        both["fields"].append({"name": "extra", "type": "string"})
        pair = self.register_all(base, both, deepcopy(both))
        self.assertEqual([f["name"] for f in self.w.merge_schema(*pair)["fields"]],
                         ["keep", "drop", "extra"])

    def test_enum_and_alias_attributes(self):
        base = {"type": "record", "name": "pkg.R", "aliases": ["old.R"], "fields": [
            {"name": "e", "type": {"type": "enum", "name": "pkg.E",
                                   "symbols": ["A", "B"], "default": "A"}}]}
        left, right = deepcopy(base), deepcopy(base)
        left["aliases"] = ["old.R", "older.R"]
        left["fields"][0]["type"]["symbols"] = ["A", "B", "C"]
        right["fields"][0]["type"]["default"] = "B"
        revisions = self.register_all(base, left, right)
        merged = self.w.merge_schema(*revisions)
        parsed = Schema(merged)
        self.assertEqual(parsed.names["pkg.R"].aliases, ("old.R", "older.R"))
        self.assertEqual(parsed.names["pkg.E"].symbols, ("A", "B", "C"))
        self.assertEqual(parsed.names["pkg.E"].enum_default, "B")
        self.w.append("left", revisions[1], [{"e": "C"}])
        created = self.w.create_merged_read("e", *revisions, ["left"])
        self.assertEqual(self.w.run(created["task_id"])["rows"], [{"e": "C"}])

    def test_unreachable_definition_still_checked(self):
        base = {"type": "record", "name": "R", "fields": [
            {"name": "a", "type": {"type": "record", "name": "N", "fields": [
                {"name": "v", "type": "int"}]}},
            {"name": "b", "type": "N"}]}
        left = deepcopy(base)
        left["fields"][0]["type"]["fields"][0]["type"] = "long"
        left["fields"][1]["type"] = "int"
        right = {"type": "record", "name": "R", "fields": [
            {"name": "a", "type": "int"},
            {"name": "b", "type": {"type": "record", "name": "N", "fields": [
                {"name": "v", "type": "int"}]}}]}
        revisions = self.register_all(base, left, right)
        merged = self.w.merge_schema(*revisions)
        self.assertEqual(sorted(Schema(merged).names), ["R"])
        self.assertEqual([f["name"] for f in merged["fields"]], ["a", "b"])
        conflicting = deepcopy(right)
        conflicting["fields"][1]["type"]["fields"][0]["type"] = "string"
        pair = self.register_all(base, left, conflicting)
        with self.assertRaises(MergeConflict):
            self.w.merge_schema(*pair)

    def test_conflicts_are_located_stable_and_pure(self):
        for label, base, left, right in conflict_trios():
            with self.subTest(label=label):
                revisions = self.register_all(base, left, right)
                before = self.snapshot()
                reports = []
                for _ in range(2):
                    with self.assertRaises(MergeConflict) as caught:
                        self.w.merge_schema(*revisions)
                    exc = caught.exception
                    self.assertIsInstance(exc, Conflict)
                    for attribute in (exc.definition, exc.path, exc.reason):
                        self.assertIsInstance(attribute, str)
                        self.assertTrue(attribute)
                    reports.append((exc.definition, exc.path, exc.reason))
                self.assertEqual(reports[0], reports[1])
                self.assertEqual(self.snapshot(), before)

    def test_no_half_publication(self):
        revisions = self.register_all(node_base(), node_left(), node_right())
        self.w.append("left", revisions[1], [{"sample": 1, "next": tag(0, None)}])
        old = self.w.create_read(revisions[1], ["left"])
        before = self.snapshot()
        with self.assertRaises(NotFound):
            self.w.create_merged_read("c", *revisions, ["missing"])
        self.assertEqual(self.snapshot(), before)

        def interrupted(conn):
            raise RuntimeError("merge-publication-interrupted")
        with self.assertRaises(RuntimeError):
            self.w.create_merged_read("c", *revisions, ["left"],
                                      before_commit=interrupted)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(NotFound):
            self.w.schemas.head("c")
        created = self.w.create_merged_read("c", *revisions, ["left"])
        again = self.w.create_merged_read("c", *revisions, ["left"])
        self.assertNotEqual(created["revision"], again["revision"])
        self.assertNotEqual(created["task_id"], again["task_id"])
        self.assertEqual(self.w.schemas.head("c"), again["revision"])
        self.assertEqual(self.w.tasks.get(old)["reader_revision"], revisions[1])
        self.assertEqual(self.w.tasks.get(created["task_id"])["reader_revision"],
                         created["revision"])

    def test_read_failure_keeps_revision_and_task(self):
        base = {"type": "record", "name": "R", "fields": [
            {"name": "x", "type": ["int", "string"]}]}
        left, right = deepcopy(base), deepcopy(base)
        left["fields"][0]["type"] = ["int"]
        revisions = self.register_all(base, left, right)
        rows = [{"x": tag(0, 7)}, {"x": tag(1, "s")}, {"x": tag(0, 8)}]
        self.w.append("right", revisions[2], rows)
        created = self.w.create_merged_read("c", *revisions, ["right"])
        result = self.w.run(created["task_id"])
        self.assertEqual(result["state"], "failed")
        self.assertIsNone(result["rows"])
        self.assertEqual(result["error"]["row_index"], 1)
        self.assertEqual(result["error"]["code"], "incompatible")
        self.assertEqual(result["error"]["path"], "$.x")
        self.assertEqual(self.w.schemas.head("c"), created["revision"])
        self.assertEqual(self.w.run(created["task_id"]), result)

    def test_exact_revisions_never_heads(self):
        first = self.w.register("n", "int")
        second = self.w.register("n", "long")
        self.assertEqual(self.w.merge_schema(first, first, first), "int")
        self.assertEqual(self.w.merge_schema(first, second, second), "long")
        with self.assertRaises(NotFound):
            self.w.merge_schema(999, first, first)
        with self.assertRaises(NotFound):
            self.w.create_merged_read("c", first, first, 999, [])

    def test_parameter_validation(self):
        revision = self.w.register("n", "int")
        with self.assertRaises(SchemaError):
            self.w.create_merged_read("", revision, revision, revision, [])
        with self.assertRaises(PayloadError):
            self.w.create_merged_read("c", revision, revision, revision, "x")
        with self.assertRaises(PayloadError):
            self.w.create_merged_read("c", revision, revision, revision, [1])


if __name__ == "__main__":
    unittest.main()
