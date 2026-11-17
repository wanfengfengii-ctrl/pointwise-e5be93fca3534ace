from . import Warehouse
from .jsonutil import clone


def tagged(branch, value):
    return {"branch": branch, "value": value}


def node_schema(reader=False, enum_default=False):
    enum = {"type": "enum", "name": "Code", "namespace": "research",
            "symbols": ["A", "UNKNOWN"] if reader else ["A", "B"]}
    if reader and enum_default:
        enum["default"] = "UNKNOWN"
    value = [enum, "long"] if reader else ["int", enum]
    return {"type": "record", "name": "Node", "namespace": "research",
            "fields": [
                ({"name": "label", "type": "string", "aliases": ["name"]}
                 if reader else {"name": "name", "type": "string"}),
                {"name": "value", "type": value},
                {"name": "next", "type": ["null", "Node"]}]}


def node(name, value, next_node=None):
    return {"name": name, "value": clone(value),
            "next": tagged(0, None) if next_node is None else tagged(1, next_node)}


def populate(warehouse):
    writer = warehouse.register("observations", node_schema())
    strict = warehouse.register("reading", node_schema(True))
    fallback = warehouse.register("reading", node_schema(True, True))
    first = node("root", tagged(0, 7), node("leaf", tagged(0, -4)))
    bad = node("bad", tagged(1, "B"))
    warehouse.append("good", writer, [first, first])
    warehouse.append("mixed", writer, [first, bad, bad])
    warehouse.append("empty", writer, [])
    tasks = {
        "pending": warehouse.create_read(strict, ["good", "empty", "good"]),
        "success": warehouse.create_read(fallback, ["mixed"]),
        "failure": warehouse.create_read(strict, ["mixed"])}
    warehouse.run(tasks["success"])
    warehouse.run(tasks["failure"])
    return {"writer": writer, "strict": strict, "fallback": fallback, "tasks": tasks}


def create_frozen(path):
    """Construct a real complete legacy database; caller owns its path."""
    with Warehouse(path) as warehouse:
        return populate(warehouse)


def recursive_program():
    """Native SDK2 example: project labels and preserve recursive tail tags."""
    return {"entry": "node", "nodes": {
        "node": {"op": "record", "fields": [
            {"name": "label", "source": "name", "next": "text"},
            {"name": "next", "source": "next", "next": "tail"}]},
        "text": {"op": "check", "kind": "string", "next": "copy"},
        "copy": {"op": "copy"},
        "tail": {"op": "switch", "branches": ["null", "more"]},
        "null": {"op": "check", "kind": "null", "next": "null_out"},
        "null_out": {"op": "wrap", "branch": 0, "next": "copy"},
        "more": {"op": "wrap", "branch": 1, "next": "recur"},
        "recur": {"op": "call", "target": "node"}}}
