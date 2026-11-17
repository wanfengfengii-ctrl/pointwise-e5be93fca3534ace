"""Three-way schema confluence per MERGING.md.

Pure computation over detached SDK1 schemas: named definitions correspond by
qualified full name, record fields correspond by base identity (same name
first, then aliases carrying the base name), ordinary attributes follow
three-way rules, and selected defaults are re-encoded onto the merged field
names and union order without borrowing reader projection, enum fallback or
record filling.  Nothing here touches catalog, batch or task state.
"""
from vendor.sdk1 import Schema
from .errors import MergeConflict, SchemaError
from .jsonutil import clone

ABSENT = object()

_SIDES = ("base", "left", "right")
_BOUNDS = {"int": (-(1 << 31), 1 << 31), "long": (-(1 << 63), 1 << 63)}


def merge_schemas(base, left, right):
    """Merge three detached raw schemas into one detached SDK1 schema."""
    raw = _Merger(base, left, right).emit()
    try:
        Schema(raw)
    except SchemaError as exc:
        raise MergeConflict("$", "$", "merged result is not a valid SDK1 schema: "
                            + str(exc)) from exc
    return clone(raw)


class _Merger:
    def __init__(self, base, left, right):
        self.schema = {side: Schema(raw) for side, raw
                       in zip(_SIDES, (base, left, right))}
        self.identities = {}  # record fullname -> {side: {field name: identity}}
        self.merged = {}      # fullname -> merged definition model
        self.root = None
        self._check_definitions()
        self.names = sorted(self.schema["base"].names)
        self._compute_identities()
        for fullname in self.names:
            self._skeleton(fullname)
        for fullname in self.names:
            self._body(fullname)
        self.root = self._merge_type(*(self.schema[side].root for side in _SIDES),
                                     "$", "$")
        for fullname in self.names:
            self._defaults(fullname)

    @staticmethod
    def _conflict(definition, path, reason):
        raise MergeConflict(definition, path, reason)

    # ---- definition set and field identity ----

    def _check_definitions(self):
        sets = {side: set(self.schema[side].names) for side in _SIDES}
        for fullname in sorted(set().union(*sets.values())):
            missing = [side for side in _SIDES if fullname not in sets[side]]
            if missing:
                self._conflict(fullname, "$", "named definition missing from "
                               + "/".join(missing))
            kinds = {side: self.schema[side].names[fullname].kind for side in _SIDES
                     if fullname in sets[side]}
            if len(set(kinds.values())) > 1:
                detail = ", ".join(side + "=" + kind for side, kind in kinds.items())
                self._conflict(fullname, "$", "definition kind differs: " + detail)

    def _compute_identities(self):
        """Map every record field of every side to its base field identity."""
        for fullname in self.names:
            if self.schema["base"].names[fullname].kind != "record":
                continue
            base_fields = self.schema["base"].names[fullname].fields
            maps = {"base": {f[0]: f[0] for f in base_fields}}
            for side in ("left", "right"):
                side_fields = self.schema[side].names[fullname].fields
                used = {}
                for bf in base_fields:
                    match = self._counterpart(bf[0], side_fields)
                    if match is None:
                        continue
                    if match in used:
                        self._conflict(fullname, match,
                                       "one branch field corresponds to multiple "
                                       f"base fields ({used[match]} and {bf[0]})")
                    used[match] = bf[0]
                maps[side] = {f[0]: used.get(f[0], f[0]) for f in side_fields}
            self.identities[fullname] = maps

    @staticmethod
    def _counterpart(base_name, side_fields):
        """Same-name field wins; only without it may an alias carry the base name."""
        alias_match = None
        for f in side_fields:
            if f[0] == base_name:
                return f[0]
            if base_name in f[2]:
                alias_match = f[0]
        return alias_match

    # ---- generic three-way attribute rule ----

    @staticmethod
    def _eq(x, y):
        if x is ABSENT or y is ABSENT:
            return x is y
        return x == y

    def _three_way(self, b, l, r, definition, path, what, eq=None):
        eq = eq or self._eq
        if eq(l, b):
            if eq(r, b):
                return b
            return r
        if eq(r, b):
            return l
        if eq(l, r):
            return l
        self._conflict(definition, path, f"both sides changed {what} differently")

    # ---- phase A: surviving fields, merged names and alias sets ----

    def _skeleton(self, fullname):
        if self.schema["base"].names[fullname].kind != "record":
            self.merged[fullname] = {"kind": "enum", "name": fullname}
            return
        nodes = {side: self.schema[side].names[fullname] for side in _SIDES}
        by_name = {side: {f[0]: f for f in nodes[side].fields} for side in _SIDES}
        inverse = {}
        for side in ("left", "right"):
            inverse[side] = {}
            for field_name, identity in self.identities[fullname][side].items():
                inverse[side][identity] = field_name
        slots = []
        counterparts = {"left": set(), "right": set()}
        for bf in nodes["base"].fields:
            identity = bf[0]
            sources = {"base": bf}
            for side in ("left", "right"):
                field_name = inverse[side].get(identity)
                sources[side] = by_name[side].get(field_name) if field_name else None
                if sources[side] is not None:
                    counterparts[side].add(field_name)
            slot = self._slot(fullname, identity, sources)
            if slot is not None:
                slots.append(slot)
        additions = {}
        for side in ("left", "right"):
            for field_name, f in by_name[side].items():
                if field_name not in counterparts[side]:
                    additions.setdefault(field_name, {})[side] = f
        for field_name in sorted(additions):
            sources = {"base": None, "left": None, "right": None}
            sources.update(additions[field_name])
            slots.append(self._slot(fullname, field_name, sources))
        self.merged[fullname] = {"kind": "record", "name": fullname, "fields": slots,
                                 "by_identity": {s["identity"]: s for s in slots}}

    def _slot(self, fullname, identity, sources):
        b, l, r = (sources[side] for side in _SIDES)
        if b is not None:
            if l is None and r is None:
                return None
            if l is None:
                if not self._field_eq(b, r, "right"):
                    self._conflict(fullname, identity,
                                   "field deleted on left but modified on right")
                return None
            if r is None:
                if not self._field_eq(b, l, "left"):
                    self._conflict(fullname, identity,
                                   "field deleted on right but modified on left")
                return None
            name = self._three_way(b[0], l[0], r[0], fullname,
                                   identity + ".name", "field name")
            aliases = self._three_way(frozenset(b[2]), frozenset(l[2]),
                                      frozenset(r[2]), fullname,
                                      identity + ".aliases", "field aliases")
        else:
            present = [f for f in (l, r) if f is not None]
            name = identity
            if len(present) == 2:
                aliases = self._three_way(ABSENT, frozenset(l[2]), frozenset(r[2]),
                                          fullname, identity + ".aliases",
                                          "field aliases")
            else:
                aliases = frozenset(present[0][2])
        return {"identity": identity, "name": name, "aliases": aliases,
                "sources": sources}

    def _field_eq(self, bf, sf, side):
        if bf[0] != sf[0] or frozenset(bf[2]) != frozenset(sf[2]):
            return False
        if not self._type_eq(bf[1], sf[1]):
            return False
        return self._eq(self._default_state("base", bf),
                        self._default_state(side, sf))

    # ---- phase B: definition attributes and field types ----

    def _body(self, fullname):
        d = self.merged[fullname]
        nodes = {side: self.schema[side].names[fullname] for side in _SIDES}
        d["aliases"] = self._three_way(*(frozenset(nodes[side].aliases)
                                         for side in _SIDES),
                                       fullname, "aliases", "definition aliases")
        if d["kind"] == "enum":
            d["symbols"] = tuple(self._three_way(*(tuple(nodes[side].symbols)
                                                   for side in _SIDES),
                                                 fullname, "symbols", "enum symbols"))
            states = [nodes[side].enum_default
                      if nodes[side].enum_default is not None else ABSENT
                      for side in _SIDES]
            merged = self._three_way(*states, fullname, "default", "enum default")
            d["has_default"], d["default"] = (merged is not ABSENT,
                                              None if merged is ABSENT else merged)
            return
        for slot in d["fields"]:
            b, l, r = (slot["sources"][side] for side in _SIDES)
            if b is not None:
                slot["type"] = self._merge_type(b[1], l[1], r[1], fullname,
                                                slot["identity"] + ".type")
            elif l is not None and r is not None:
                if not self._type_eq(l[1], r[1]):
                    self._conflict(fullname, slot["identity"] + ".type",
                                   "both sides added this field with different types")
                slot["type"] = self._convert(l[1])
            else:
                slot["type"] = self._convert((l if l is not None else r)[1])

    def _merge_type(self, b, l, r, definition, path):
        named = [n for n in (b, l, r) if n.kind in ("record", "enum")]
        if len(named) == 3 and b.name == l.name == r.name:
            return ("ref", b.name)
        if b.kind == l.kind == r.kind == "array":
            return ("array", self._merge_type(b.items, l.items, r.items,
                                              definition, path))
        if b.kind == l.kind == r.kind == "union":
            return self._merge_union(b, l, r, definition, path)
        if self._type_eq(l, b) and self._type_eq(r, b):
            return self._convert(b)
        if self._type_eq(l, b):
            return self._convert(r)
        if self._type_eq(r, b):
            return self._convert(l)
        if self._type_eq(l, r):
            return self._convert(l)
        self._conflict(definition, path,
                       "incompatible type edits (base {}, left {}, right {})".format(
                           *(self._describe(n) for n in (b, l, r))))

    def _merge_union(self, b, l, r, definition, path):
        nodes = dict(zip(_SIDES, (b, l, r)))
        seqs = {side: [x.name or x.kind for x in nodes[side].branches]
                for side in _SIDES}
        merged_seq = self._three_way(seqs["base"], seqs["left"], seqs["right"],
                                     definition, path, "union branch sequence")
        branch = {side: dict(zip(seqs[side], nodes[side].branches))
                  for side in _SIDES}
        out = []
        for identity in merged_seq:
            present = {side: branch[side][identity] for side in _SIDES
                       if identity in branch[side]}
            if len(present) == 3:
                out.append(self._merge_type(*(present[side] for side in _SIDES),
                                            definition, f"{path}[{identity}]"))
            elif "base" not in present:
                if "left" in present and "right" in present:
                    if not self._type_eq(present["left"], present["right"]):
                        self._conflict(definition, f"{path}[{identity}]",
                                       "both sides added this union branch "
                                       "with different content")
                    out.append(self._convert(present["left"]))
                else:
                    side = "left" if "left" in present else "right"
                    out.append(self._convert(present[side]))
            else:
                raise AssertionError("merged union branch missing from one side")
        for identity in seqs["base"]:
            if identity in merged_seq:
                continue
            for side in ("left", "right"):
                if (identity in branch[side]
                        and not self._type_eq(branch[side][identity],
                                              branch["base"][identity])):
                    self._conflict(definition, f"{path}[{identity}]",
                                   "union branch deleted on one side but its "
                                   f"content modified on {side}")
        return ("union", tuple(out))

    def _type_eq(self, x, y):
        if x.kind != y.kind:
            return False
        if x.kind in ("record", "enum"):
            return x.name == y.name
        if x.kind == "array":
            return self._type_eq(x.items, y.items)
        if x.kind == "union":
            return (len(x.branches) == len(y.branches)
                    and all(self._type_eq(a, b)
                            for a, b in zip(x.branches, y.branches)))
        return True

    def _convert(self, node):
        if node.kind in ("record", "enum"):
            return ("ref", node.name)
        if node.kind == "array":
            return ("array", self._convert(node.items))
        if node.kind == "union":
            return ("union", tuple(self._convert(x) for x in node.branches))
        return ("prim", node.kind)

    @staticmethod
    def _describe(node):
        return node.name if node.kind in ("record", "enum") else node.kind

    # ---- phase C: defaults, interpreted then re-encoded ----

    def _default_state(self, side, field):
        if field is None or not field[3]:
            return ABSENT
        return self._canon(side, field[1], field[4])

    def _defaults(self, fullname):
        d = self.merged[fullname]
        if d["kind"] != "record":
            return
        for slot in d["fields"]:
            sources = slot.pop("sources")
            states = [self._default_state(side, sources[side]) for side in _SIDES]
            selected = self._three_way(*states, fullname,
                                       slot["identity"] + ".default",
                                       "field default")
            if selected is ABSENT:
                slot["has_default"], slot["default"] = False, None
            else:
                slot["has_default"] = True
                slot["default"] = self._encode(selected, slot["type"], fullname,
                                               slot["identity"] + ".default")

    def _canon(self, side, node, value):
        """Finite value keyed by branch identity and record field identity."""
        kind = node.kind
        if kind in ("null", "int", "long", "string"):
            return ("scalar", value)
        if kind == "enum":
            return ("enum", value)
        if kind == "union":
            branch = node.branches[value["branch"]]
            return ("union", branch.name or branch.kind,
                    self._canon(side, branch, value["value"]))
        if kind == "array":
            return ("array", tuple(self._canon(side, node.items, x) for x in value))
        idmap = self.identities[node.name][side]
        items = tuple(sorted(
            (idmap[f[0]], self._canon(side, f[1], value[f[0]]))
            for f in node.fields))
        return ("record", node.name, items)

    def _encode(self, canon, mtype, definition, path):
        """Re-encode one interpreted default onto the merged schema, or conflict."""
        tag = canon[0]
        kind = mtype[0]
        if kind == "prim":
            want = mtype[1]
            value = canon[1] if tag == "scalar" else None
            ok = (tag == "scalar"
                  and (want == "null" and value is None
                       or want in _BOUNDS and type(value) is int
                       and _BOUNDS[want][0] <= value < _BOUNDS[want][1]
                       or want == "string" and isinstance(value, str)))
            if not ok:
                self._conflict(definition, path,
                               f"default value no longer fits merged type {want}")
            return value
        if kind == "array":
            if tag != "array":
                self._conflict(definition, path,
                               "array default does not fit merged type")
            return [self._encode(x, mtype[1], definition, path + "[]")
                    for x in canon[1]]
        if kind == "union":
            if tag != "union":
                self._conflict(definition, path,
                               "union default does not fit merged type")
            identity = canon[1]
            for index, branch in enumerate(mtype[1]):
                if self._mtype_identity(branch) == identity:
                    return {"branch": index,
                            "value": self._encode(canon[2], branch,
                                                  definition, path)}
            self._conflict(definition, path,
                           f"default branch {identity} no longer exists in "
                           "the merged union")
        target = self.merged[mtype[1]]
        if target["kind"] == "enum":
            if tag != "enum" or canon[1] not in target["symbols"]:
                self._conflict(definition, path,
                               f"default symbol is not retained in merged enum "
                               f"{mtype[1]}")
            return canon[1]
        if tag != "record" or canon[1] != mtype[1]:
            self._conflict(definition, path,
                           f"record default does not match merged type {mtype[1]}")
        fields = dict(canon[2])
        if set(fields) != set(target["by_identity"]):
            self._conflict(definition, path,
                           "record default fields do not correspond one-to-one "
                           f"with the complete merged fields of {mtype[1]}")
        return {target["by_identity"][identity]["name"]:
                self._encode(value, target["by_identity"][identity]["type"],
                             definition,
                             path + "." + target["by_identity"][identity]["name"])
                for identity, value in canon[2]}

    @staticmethod
    def _mtype_identity(mtype):
        if mtype[0] == "ref":
            return mtype[1]
        if mtype[0] == "prim":
            return mtype[1]
        return mtype[0]

    # ---- emission: any equivalent declaration layout ----

    def emit(self):
        emitted = set()

        def emit_type(mtype):
            kind = mtype[0]
            if kind == "prim":
                return mtype[1]
            if kind == "array":
                return {"type": "array", "items": emit_type(mtype[1])}
            if kind == "union":
                return [emit_type(x) for x in mtype[1]]
            name = mtype[1]
            if name in emitted:
                return name
            emitted.add(name)
            return emit_def(name)

        def emit_def(name):
            d = self.merged[name]
            out = {"type": d["kind"], "name": name}
            if d["aliases"]:
                out["aliases"] = sorted(d["aliases"])
            if d["kind"] == "enum":
                out["symbols"] = list(d["symbols"])
                if d["has_default"]:
                    out["default"] = d["default"]
                return out
            fields = []
            for slot in d["fields"]:
                f = {"name": slot["name"], "type": emit_type(slot["type"])}
                if slot["aliases"]:
                    f["aliases"] = sorted(slot["aliases"])
                if slot["has_default"]:
                    f["default"] = slot["default"]
                fields.append(f)
            out["fields"] = fields
            return out

        return emit_type(self.root)
