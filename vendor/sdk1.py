"""Offline schema-resolution dependency, generation 1."""
import re
from dataclasses import dataclass, field
from warehouse.errors import SchemaError, PayloadError, ResolutionError
from warehouse.jsonutil import clone

PRIMITIVES = {"null", "int", "long", "string"}
IDENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")


def fullname(name, namespace=""):
    if not isinstance(name, str) or not name:
        raise SchemaError("invalid name")
    result = name if "." in name or not namespace else namespace + "." + name
    if any(not IDENT.fullmatch(part) for part in result.split(".")):
        raise SchemaError("invalid qualified name")
    return result


@dataclass(eq=False)
class Node:
    kind: str
    name: str = ""
    aliases: tuple = ()
    fields: list = field(default_factory=list)
    branches: list = field(default_factory=list)
    items: object = None
    symbols: tuple = ()
    enum_default: object = None


class Schema:
    def __init__(self, raw):
        try:
            self.raw = clone(raw)
        except PayloadError as exc:
            raise SchemaError("schema must be finite JSON") from exc
        self.names, self.definitions, self.objects = {}, {}, {}
        self.built = set()
        self._collect(self.raw, "")
        self.root = self._parse(self.raw, "")
        for node in self.names.values():
            if node.kind == "record":
                for name, child, aliases, has_default, default in node.fields:
                    if has_default:
                        try:
                            validate_node(child, default)
                        except PayloadError as exc:
                            raise SchemaError(f"invalid default for {name}: {exc}") from exc

    def _collect(self, raw, ns):
        if isinstance(raw, list):
            for child in raw:
                self._collect(child, ns)
        elif isinstance(raw, dict):
            kind = raw.get("type")
            if kind in ("record", "enum"):
                namespace = raw.get("namespace", ns)
                if not isinstance(namespace, str):
                    raise SchemaError("namespace must be text")
                if namespace:
                    fullname(namespace)
                name = fullname(raw.get("name"), namespace)
                child_ns = name.rpartition(".")[0]
                if name in self.names:
                    raise SchemaError("duplicate named definition")
                aliases = raw.get("aliases", [])
                if not isinstance(aliases, list):
                    raise SchemaError("aliases must be a list")
                aliases = tuple(fullname(a, child_ns) for a in aliases)
                if len(set(aliases)) != len(aliases) or name in aliases:
                    raise SchemaError("duplicate named alias")
                node = Node(kind, name=name, aliases=aliases)
                self.names[name] = node
                self.definitions[name] = (raw, child_ns)
                self.objects[id(raw)] = node
                if kind == "record":
                    fields = raw.get("fields")
                    if not isinstance(fields, list):
                        raise SchemaError("record fields must be a list")
                    for entry in fields:
                        if not isinstance(entry, dict) or "type" not in entry:
                            raise SchemaError("invalid field")
                        self._collect(entry["type"], child_ns)
            elif kind == "array":
                if "items" not in raw:
                    raise SchemaError("array requires items")
                self._collect(raw["items"], ns)

    def _parse(self, raw, ns):
        if isinstance(raw, list):
            if not raw:
                raise SchemaError("empty union")
            branches = [self._parse(x, ns) for x in raw]
            keys = [x.name or x.kind for x in branches]
            if any(x.kind == "union" for x in branches) or len(set(keys)) != len(keys):
                raise SchemaError("nested or duplicate union branch")
            return Node("union", branches=branches)
        if isinstance(raw, str):
            if raw in PRIMITIVES:
                return Node(raw)
            name = fullname(raw, ns)
            if name not in self.names:
                raise SchemaError("unresolved reference: " + name)
            return self._build(self.names[name])
        if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
            raise SchemaError("schema must be a type, union, or type object")
        kind = raw["type"]
        if kind in ("record", "enum"):
            return self._build(self.objects[id(raw)])
        if kind == "array":
            return Node("array", items=self._parse(raw["items"], ns))
        return self._parse(kind, ns)

    def _build(self, node):
        if node.name in self.built:
            return node
        self.built.add(node.name)
        raw, ns = self.definitions[node.name]
        if node.kind == "enum":
            symbols = raw.get("symbols")
            if (not isinstance(symbols, list) or not symbols
                    or any(not isinstance(s, str) or not IDENT.fullmatch(s) for s in symbols)
                    or len(set(symbols)) != len(symbols)):
                raise SchemaError("invalid enum symbols")
            node.symbols = tuple(symbols)
            if "default" in raw and raw["default"] not in symbols:
                raise SchemaError("invalid enum default")
            node.enum_default = raw.get("default")
        else:
            tokens = set()
            for entry in raw["fields"]:
                name = entry.get("name")
                aliases = entry.get("aliases", [])
                if (not isinstance(name, str) or not IDENT.fullmatch(name)
                        or not isinstance(aliases, list)
                        or any(not isinstance(a, str) or not IDENT.fullmatch(a) for a in aliases)):
                    raise SchemaError("invalid field name or aliases")
                keys = [name] + aliases
                if len(set(keys)) != len(keys) or tokens.intersection(keys):
                    raise SchemaError("ambiguous field aliases")
                tokens.update(keys)
                node.fields.append((name, self._parse(entry["type"], ns),
                                    tuple(aliases), "default" in entry, entry.get("default")))
        return node

    def validate(self, value):
        validate_node(self.root, value)
        return clone(value)


def validate_node(node, value, path="$"):
    kind = node.kind
    if kind == "null":
        valid = value is None
    elif kind in ("int", "long"):
        bits = 32 if kind == "int" else 64
        valid = type(value) is int and -(1 << (bits - 1)) <= value < (1 << (bits - 1))
    elif kind == "string":
        valid = isinstance(value, str)
    elif kind == "enum":
        valid = isinstance(value, str) and value in node.symbols
    elif kind == "array":
        valid = isinstance(value, list)
        if valid:
            for i, child in enumerate(value):
                validate_node(node.items, child, f"{path}[{i}]")
    elif kind == "record":
        valid = isinstance(value, dict) and set(value) == {f[0] for f in node.fields}
        if valid:
            for name, child, *_ in node.fields:
                validate_node(child, value[name], path + "." + name)
    elif kind == "union":
        valid = (isinstance(value, dict) and set(value) == {"branch", "value"}
                 and type(value["branch"]) is int and 0 <= value["branch"] < len(node.branches))
        if valid:
            validate_node(node.branches[value["branch"]], value["value"], path)
    else:
        valid = False
    if not valid:
        raise PayloadError(f"invalid {kind} at {path}")


def compatible(writer, reader, seen=None):
    """Branch selection predicate; record children and enum symbols are runtime checks."""
    seen = set() if seen is None else seen
    pair = (id(writer), id(reader))
    if pair in seen:
        return True
    seen = seen | {pair}
    if writer.kind == "union":
        return any(compatible(w, reader, seen) for w in writer.branches)
    if reader.kind == "union":
        return any(compatible(writer, r, seen) for r in reader.branches)
    if writer.kind == "int" and reader.kind == "long":
        return True
    if writer.kind != reader.kind:
        return False
    if writer.kind in ("record", "enum"):
        return writer.name == reader.name or writer.name in reader.aliases
    if writer.kind == "array":
        return compatible(writer.items, reader.items, seen)
    return True


class Resolver:
    def __init__(self, writer, reader):
        self.writer = writer if isinstance(writer, Schema) else Schema(writer)
        self.reader = reader if isinstance(reader, Schema) else Schema(reader)

    def read(self, value):
        self.writer.validate(value)
        return self._read(self.writer.root, self.reader.root, value, "$")

    def _read(self, writer, reader, value, path):
        if writer.kind == "union":
            return self._read(writer.branches[value["branch"]], reader, value["value"], path)
        if reader.kind == "union":
            for i, branch in enumerate(reader.branches):
                if compatible(writer, branch):
                    return {"branch": i, "value": self._read(writer, branch, value, path)}
            raise ResolutionError("incompatible", path, "no reader union branch")
        if not compatible(writer, reader):
            raise ResolutionError("incompatible", path, writer.kind + " -> " + reader.kind)
        if reader.kind == "record":
            result = {}
            for name, child, aliases, has_default, default in reader.fields:
                matches = [f for f in writer.fields if f[0] == name or f[0] in aliases]
                if len(matches) > 1:
                    raise ResolutionError("ambiguous_field", path + "." + name,
                                          "multiple writer fields match")
                if not matches:
                    if not has_default:
                        raise ResolutionError("missing_field", path + "." + name, "no default")
                    result[name] = clone(default)
                else:
                    source = matches[0]
                    result[name] = self._read(source[1], child, value[source[0]], path + "." + name)
            return result
        if reader.kind == "array":
            return [self._read(writer.items, reader.items, x, f"{path}[{i}]")
                    for i, x in enumerate(value)]
        if reader.kind == "enum":
            if value in reader.symbols:
                return value
            if reader.enum_default is not None:
                return reader.enum_default
            raise ResolutionError("enum_symbol", path, "reader has no symbol " + value)
        return clone(value)
