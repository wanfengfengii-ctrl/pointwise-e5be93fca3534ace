# Offline dependency ports

Both generations use finite JSON values. Integer arithmetic uses Python int,
never float. This is a limited research schema dialect, not Avro wire encoding.
Both ports are public executable dependencies, not network packages.

## SDK1 schema language

A schema is a primitive name (null, int, long, string), a nonempty list of
schemas (union), or an object with type. Primitive objects such as
{"type":"int"} are accepted. Arrays use {"type":"array","items":schema}.
Named definitions are record objects with name, optional namespace and aliases,
and fields, or enum objects with name, optional namespace and aliases, symbols,
and optional default. Record fields have name, type, optional aliases and default.
Descriptive extra keys are ignored. Unknown types and missing references fail.
Nested unions and duplicate branch identities are rejected. A branch identity
is its qualified name for record/enum, otherwise its kind.

Names consist of dot-separated ASCII identifiers. An unqualified definition
uses its namespace, or its enclosing record's namespace. A qualified definition
ignores namespace for qualification. Unqualified references resolve in the
enclosing definition's namespace; qualified references are exact. Named
definitions are collected before resolution, so forward and mutual references
are legal. A name has one definition per schema. Named aliases are qualified
relative to that definition. Aliases do not create definition/reference names.

Each record's field names and aliases must be pairwise disjoint, including
a field's own name. Each enum has nonempty, distinct identifier symbols; its
default, when present, must be one of those symbols. Field defaults must be
valid values under their own reader type. Union defaults use the explicit
tagged representation too; any valid branch is allowed, not just branch zero.

## Writer payloads

null is JSON null; string is JSON text. int is a signed 32-bit integer, long a
signed 64-bit integer. Booleans and floats are not integers. Enums are strings
from the writer's symbols. Arrays are lists, records are objects with exactly
their declared field names. Writer defaults never fill omitted payload fields.
Every union value is {"branch": zero_based_index, "value": payload}. Even a null
branch is explicitly tagged. A bool is not a valid tag. Only the selected
writer branch's payload is validated. JSON values must be finite.

Schema(raw).validate(value) returns a detached copy or raises PayloadError.
Constructing Schema raises SchemaError for invalid declarations. Resolver
accepts Schema instances or raw schema JSON; read(value) first validates the
writer payload, then resolves it.

## Branch compatibility and runtime resolution

The branch predicate is public as sdk1.compatible(writer_node, reader_node).
It is deliberately separate from all-value compatibility:

1. A writer union is compatible when some writer branch is compatible with the
   reader. A reader union is compatible when some reader branch is compatible
   with the writer. Recursion is compared coinductively by node pair.
2. int matches long; equal primitive kinds match. No other promotion exists.
3. Records and enums match when kinds agree and the writer full name equals
   the reader full name or occurs in the reader's qualified aliases. Record
   child fields and enum symbol coverage are not part of branch selection.
4. Arrays match when their item schemas satisfy this predicate.

For each actual value, dispatch a writer union by its original payload tag
BEFORE selecting a reader branch. A reader union chooses the first compatible
branch in declaration order, emits its new tag, and resolves only that branch.
A selected branch's failure is final; no later branch is tried. An unvisited
writer branch cannot cause a row to fail.

For matched records, visit fields in reader declaration order. A writer field
matches the reader name or one of the reader field's aliases. Match only
writer field names, not writer aliases. More than one match is ambiguous_field;
zero matches uses a detached reader default or raises missing_field. Extra
writer fields are ignored. A selected matching field is recursively resolved.
A record name match can therefore fail in its child field at runtime.

Arrays retain order and multiplicity and resolve items left to right.
An enum symbol is retained if declared by the reader, otherwise replaced with
the reader enum default if any, otherwise enum_symbol is raised at the current
path. The existence of other unmapped writer symbols is not an early error.
Primitive values are copied exactly. int-to-long changes schema identity but
not the JSON integer value. Incompatible selected pairs raise incompatible.

Paths start at $, append reader field names with a dot, and array indices in
brackets. Union tags do not add path components. ResolutionError exposes code,
path and message. Warehouse reads archive the first visited error and no rows.

Examples: writer [int,string], reader [string,long], tagged int 7 produces
{"branch":1,"value":7}. Writer [int,E{A,B}], reader [E{A},long] accepts an int
row but fails on an actual E symbol B. Adding a reader enum default allows B.
If two reader enum branches alias E and the first lacks B, the second branch's
support for B cannot rescue the first branch.

## SDK2 explicit JSON programs

Program(document) validates, owns and serializes an instruction graph.
The document is {"entry":"node_id","nodes":{"node_id": instruction,...}}.
All IDs and references are strings; references must exist. Cyclic call graphs
are legal and permit finite recursive data processing. Programs must terminate
on their intended inputs. There is no implicit schema processing, branch
matching, name lookup, fallback, pair compilation or storage migration.

Each instruction receives one JSON value. Evaluation returns one JSON value or
raises ExecutionError(code,path,message). Child failures propagate unchanged.

- copy: return a detached input.
- const, value: return a fresh JSON constant.
- fail, code, message: raise explicitly at the current path.
- call, target: evaluate that node on the same value and path.
- check, kind, next: check null/int/long/string/array/record, then evaluate next.
  Integer checks enforce exact signed bounds. Record checks also require keys
  (a distinct list of field names) and reject missing or extra keys.
- record, fields: construct output fields in list order. Each field has name,
  source and next. A string source selects an input object key; missing keys
  raise missing_field at the OUTPUT field's path. source=null passes the whole
  input to the child, allowing constants or explicit failures. The input must
  be an object. This constructor does not automatically reject extra input keys.
- array, item: require a list and evaluate item on each element in order,
  extending the path with its index.
- switch, branches: require a valid tagged union with a tag within this list,
  dispatch only that branch on the unwrapped value, retaining the current path.
- wrap, branch, next: evaluate next, then construct a tagged union using branch.
- enum, mapping, optional default: require a string, map a known symbol or return
  the default string; otherwise raise enum_symbol. Mapping values are strings.

No instruction catches a child error. Malformed program structure raises
ProgramError; bad data raises ExecutionError. The VM contains no SDK1 imports.
Programs can be JSON-serialized and executed in a fresh process using SDK2 alone.
samples.recursive_program is a small native recursive label projection; it
does not generate programs from schemas.

## Persisted data

schema_v1.sql defines the complete active format: schema_revisions, schema_heads,
batches, read_tasks, published_results, read_receipts and metadata version=1.
Rows and schemas use canonical finite JSON text. Batch receipt is archive:ID.
Task receipt is read:ID:success or read:ID:failed. IDs and fixed references
remain stable over a database reopen. There is no deletion or revision update
API. The supported worker owns its database mutations.

schema_v2.sql is an unused empty destination schema. v2_revisions names a
validator program, v2_bindings names a writer/reader pair program. The remaining
tables can store archives, fixed requests and complete outcomes. There are no
populated target programs or migrated bindings in the normal project.
