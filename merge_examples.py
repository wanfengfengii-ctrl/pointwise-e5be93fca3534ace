"""Finite public inputs and hand-computed outcomes; no merge implementation."""
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from vendor.sdk1 import Schema, Resolver
from warehouse import Warehouse, NotFound


def tag(branch, value):
    return {"branch": branch, "value": value}


def node_schema():
    return {"type": "record", "name": "research.Node", "fields": [
        {"name": "id", "type": "int"},
        {"name": "next", "type": ["null", "research.Node"],
         "default": tag(0, None)}]}


def basic_case():
    base = node_schema()
    left, right, expected = (deepcopy(base) for _ in range(3))
    left["fields"][0].update(name="sample", aliases=["id"])
    right["fields"][0]["type"] = "long"
    right["fields"][1].update(type=["research.Node", "null"], default=tag(1, None))
    expected["fields"][0].update(name="sample", aliases=["id"], type="long")
    expected["fields"][1].update(type=["research.Node", "null"], default=tag(1, None))
    return base, left, right, expected


def union_case():
    base = {"type": "record", "name": "R", "fields": [
        {"name": "x", "type": ["int", "long"], "default": tag(0, 7)}]}
    left, right, expected = (deepcopy(base) for _ in range(3))
    left["fields"][0].update(type=["long", "int"], default=tag(1, 7))
    right["fields"][0]["default"] = tag(0, 9)
    expected["fields"][0].update(type=["long", "int"], default=tag(1, 9))
    return base, left, right, expected


def nested_case():
    variants = []
    for index, schema in enumerate(basic_case()):
        renamed = index in (1, 3)
        reordered = index in (2, 3)
        value = 9 if index in (2, 3) else 7
        row = {"sample" if renamed else "id": value,
               "next": tag(1 if reordered else 0, None)}
        variants.append({"type": "record", "name": "Envelope", "fields": [
            {"name": "prototype", "type": schema, "default": deepcopy(row)},
            {"name": "copies", "type": {"type": "array", "items": "research.Node"},
             "default": [deepcopy(row), deepcopy(row)]}]})
    return tuple(variants)


def conflict_cases():
    base, left, right, _ = basic_case()
    left = deepcopy(base)
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


def shape(raw):
    """Compare one parsed schema, ignoring declaration placement and documentation."""
    schema = Schema(raw)
    def ref(n):
        if n.name:
            return ("named", n.kind, n.name)
        if n.kind == "union":
            return ("union", tuple(ref(x) for x in n.branches))
        if n.kind == "array":
            return ("array", ref(n.items))
        return (n.kind,)
    definitions = []
    for name, n in sorted(schema.names.items()):
        if n.kind == "record":
            body = tuple((f, ref(t), tuple(sorted(a)), present,
                          json.dumps(d, sort_keys=True) if present else None)
                         for f, t, a, present, d in n.fields)
        else:
            body = (n.symbols, n.enum_default)
        definitions.append((name, n.kind, tuple(sorted(n.aliases)), body))
    return ref(schema.root), tuple(definitions)


def fixture_check():
    for case in (basic_case, union_case, nested_case):
        for schema in case():
            Schema(schema)
    for _, base, left, right in conflict_cases():
        for schema in (base, left, right):
            Schema(schema)
    _, left, right, expected = basic_case()
    lrow = {"sample": 7, "next": tag(1, {"sample": -2, "next": tag(0, None)})}
    rrow = {"id": 2**40, "next": tag(1, None)}
    assert Resolver(left, expected).read(lrow) == {
        "sample": 7, "next": tag(0, {"sample": -2, "next": tag(1, None)})}
    assert Resolver(right, expected).read(rrow) == {
        "sample": 2**40, "next": tag(1, None)}
    missing = {"type": "record", "name": "research.Node", "fields": [
        {"name": "id", "type": "long"}]}
    assert Resolver(missing, expected).read({"id": 11}) == {
        "sample": 11, "next": tag(1, None)}
    empty = {"type": "record", "name": "Envelope", "fields": []}
    nested_expected = nested_case()[3]
    assert Resolver(empty, nested_expected).read({}) == {
        "prototype": {"sample": 9, "next": tag(1, None)},
        "copies": [{"sample": 9, "next": tag(1, None)}] * 2}
    _, _, source, target = union_case()
    original = source["fields"][0]["default"]
    got = Resolver(source["fields"][0]["type"], target["fields"][0]["type"]).read(original)
    assert got == tag(0, 9)
    assert target["fields"][0]["default"] == tag(1, 9)
    print(json.dumps({"fixtures": "valid", "resolver_default_tag": got["branch"],
                      "identity_preserving_tag": 1}, sort_keys=True))


def register_case(w, case):
    return [w.register("branch-" + str(i), raw) for i, raw in enumerate(case[:3])]


def snapshot(w):
    tables = ("schema_revisions", "schema_heads", "read_tasks")
    return [list(map(tuple, w.db.connection.execute("SELECT * FROM " + table)))
            for table in tables]


def exercise(w):
    cases = (basic_case, union_case, nested_case)
    for factory in cases:
        case = factory()
        revisions = register_case(w, case)
        before = snapshot(w)
        assert shape(w.merge_schema(*revisions)) == shape(case[3])
        assert snapshot(w) == before
    for label, base, left, right in conflict_cases():
        revisions = register_case(w, (base, left, right))
        before = snapshot(w)
        errors = []
        for _ in range(2):
            try:
                w.merge_schema(*revisions)
            except Exception as exc:
                assert type(exc).__name__ == "MergeConflict", (label, type(exc))
                assert isinstance(exc.definition, str) and exc.definition
                assert isinstance(exc.path, str) and exc.path
                assert isinstance(exc.reason, str) and exc.reason
                errors.append((exc.definition, exc.path, exc.reason))
            else:
                raise AssertionError("expected conflict: " + label)
        assert errors[0] == errors[1]
        assert snapshot(w) == before
    base, left, right, expected = basic_case()
    revisions = register_case(w, (base, left, right))
    lrow = {"sample": 7, "next": tag(1, {"sample": -2, "next": tag(0, None)})}
    rrow = {"id": 2**40, "next": tag(1, None)}
    w.append("left", revisions[1], [lrow])
    w.append("right", revisions[2], [rrow])
    old = w.create_read(revisions[1], ["left"])
    before = snapshot(w)
    try:
        w.create_merged_read("combined", *revisions, ["missing"])
    except NotFound:
        pass
    else:
        raise AssertionError("missing batch accepted")
    assert snapshot(w) == before
    def interrupted(conn):
        raise RuntimeError("merge-publication-interrupted")
    try:
        w.create_merged_read("combined", *revisions, ["left", "right"],
                             before_commit=interrupted)
    except RuntimeError as exc:
        assert str(exc) == "merge-publication-interrupted"
    else:
        raise AssertionError("observer was not called")
    assert snapshot(w) == before
    created = w.create_merged_read("combined", *revisions, ["left", "right", "left"])
    revision, task = created["revision"], created["task_id"]
    assert shape(w.schemas.get(revision)) == shape(expected)
    assert w.schemas.head("combined") == revision
    assert w.tasks.get(task)["reader_revision"] == revision
    assert w.tasks.get(task)["batch_ids"] == ["left", "right", "left"]
    expected_rows = [
        {"sample": 7, "next": tag(0, {"sample": -2, "next": tag(1, None)})},
        {"sample": 2**40, "next": tag(1, None)},
        {"sample": 7, "next": tag(0, {"sample": -2, "next": tag(1, None)})}]
    w.register("combined", "string")
    assert w.tasks.get(old)["reader_revision"] == revisions[1]
    assert w.run(old)["rows"] == [lrow]
    return task, expected_rows


def verify():
    fixture_check()
    suite = unittest.defaultTestLoader.discover("tests")
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
    with Warehouse() as w:
        task, rows = exercise(w)
        assert w.run(task)["rows"] == rows
    demo()


def demo():
    with TemporaryDirectory(prefix="merged-read-") as directory:
        path = Path(directory) / "warehouse.sqlite"
        with Warehouse(path) as w:
            task, rows = exercise(w)
        with Warehouse(path) as w:
            result = w.run(task)
            assert result["state"] == "success" and result["rows"] == rows
            assert w.run(task) == result
            print(json.dumps({"reopened_merged_read": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    {"fixtures": fixture_check, "verify": verify, "demo": demo}[sys.argv[1]]()
