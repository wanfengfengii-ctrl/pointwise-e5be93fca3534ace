"""Generation 2: JSON programs only. No schemas or implicit resolution."""
import json


class ProgramError(ValueError):
    pass


class ExecutionError(ValueError):
    def __init__(self, code, path, message):
        self.code, self.path, self.message = code, path, message
        super().__init__(f"{code} at {path}: {message}")


def copy_json(value):
    return json.loads(json.dumps(value, allow_nan=False))


class Program:
    def __init__(self, document):
        try:
            self.document = copy_json(document)
            self.nodes = self.document["nodes"]
            self.entry = self.document["entry"]
            if not isinstance(self.nodes, dict) or not self.nodes or self.entry not in self.nodes:
                raise ValueError("invalid entry")
            for name, node in self.nodes.items():
                if not isinstance(name, str) or not isinstance(node, dict):
                    raise ValueError("invalid node")
                op = node["op"]
                references = []
                if op == "call":
                    references = [node["target"]]
                elif op == "record":
                    fields = node["fields"]
                    if not isinstance(fields, list):
                        raise ValueError("invalid fields")
                    names = []
                    for f in fields:
                        names.append(f["name"])
                        if not isinstance(f["name"], str) or not (f["source"] is None or isinstance(f["source"], str)):
                            raise ValueError("invalid field")
                        references.append(f["next"])
                    if len(set(names)) != len(names):
                        raise ValueError("duplicate output field")
                elif op == "array":
                    references = [node["item"]]
                elif op == "switch":
                    references = node["branches"]
                    if not isinstance(references, list) or not references:
                        raise ValueError("invalid switch")
                elif op == "wrap":
                    if type(node["branch"]) is not int or node["branch"] < 0:
                        raise ValueError("invalid branch")
                    references = [node["next"]]
                elif op == "check":
                    if node["kind"] not in ("null", "int", "long", "string", "record", "array"):
                        raise ValueError("invalid check")
                    references = [node["next"]]
                    if node["kind"] == "record":
                        keys = node["keys"]
                        if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
                            raise ValueError("invalid record keys")
                elif op == "enum":
                    if not isinstance(node["mapping"], dict) or any(
                            not isinstance(k, str) or not isinstance(v, str)
                            for k, v in node["mapping"].items()):
                        raise ValueError("invalid enum mapping")
                    if "default" in node and not isinstance(node["default"], str):
                        raise ValueError("invalid enum default")
                elif op == "const":
                    if "value" not in node:
                        raise ValueError("constant missing")
                elif op == "fail":
                    if not isinstance(node["code"], str) or not isinstance(node["message"], str):
                        raise ValueError("invalid failure")
                elif op != "copy":
                    raise ValueError("unknown opcode")
                if any(not isinstance(ref, str) or ref not in self.nodes for ref in references):
                    raise ValueError("dangling reference")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProgramError(str(exc)) from exc

    def to_json(self):
        return copy_json(self.document)

    def execute(self, value):
        return self._eval(self.entry, value, "$")

    def _eval(self, reference, value, path):
        n = self.nodes[reference]
        op = n["op"]
        if op == "copy":
            return copy_json(value)
        if op == "const":
            return copy_json(n["value"])
        if op == "fail":
            raise ExecutionError(n["code"], path, n["message"])
        if op == "call":
            return self._eval(n["target"], value, path)
        if op == "check":
            kind = n["kind"]
            if kind == "null":
                valid = value is None
            elif kind in ("int", "long"):
                bits = 32 if kind == "int" else 64
                valid = type(value) is int and -(1 << (bits - 1)) <= value < (1 << (bits - 1))
            elif kind == "string":
                valid = isinstance(value, str)
            elif kind == "record":
                valid = isinstance(value, dict) and set(value) == set(n["keys"])
            else:
                valid = isinstance(value, list)
            if not valid:
                raise ExecutionError("invalid_payload", path, "expected " + kind)
            return self._eval(n["next"], value, path)
        if op == "wrap":
            return {"branch": n["branch"], "value": self._eval(n["next"], value, path)}
        if op == "switch":
            if (not isinstance(value, dict) or set(value) != {"branch", "value"}
                    or type(value["branch"]) is not int
                    or not 0 <= value["branch"] < len(n["branches"])):
                raise ExecutionError("invalid_union", path, "invalid tagged union")
            return self._eval(n["branches"][value["branch"]], value["value"], path)
        if op == "array":
            if not isinstance(value, list):
                raise ExecutionError("invalid_payload", path, "expected array")
            return [self._eval(n["item"], x, f"{path}[{i}]") for i, x in enumerate(value)]
        if op == "record":
            if not isinstance(value, dict):
                raise ExecutionError("invalid_payload", path, "expected record")
            result = {}
            for f in n["fields"]:
                child_path = path + "." + f["name"]
                if f["source"] is None:
                    source = value
                elif f["source"] in value:
                    source = value[f["source"]]
                else:
                    raise ExecutionError("missing_field", child_path, "missing " + f["source"])
                result[f["name"]] = self._eval(f["next"], source, child_path)
            return result
        if op == "enum":
            if not isinstance(value, str):
                raise ExecutionError("invalid_payload", path, "expected enum symbol")
            if value in n["mapping"]:
                return n["mapping"][value]
            if "default" in n:
                return n["default"]
            raise ExecutionError("enum_symbol", path, "reader has no symbol " + value)
        raise ProgramError("unreachable opcode")
