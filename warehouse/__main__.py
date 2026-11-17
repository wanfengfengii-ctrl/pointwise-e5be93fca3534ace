import argparse
import json
from tempfile import TemporaryDirectory
from pathlib import Path
from . import Warehouse
from .samples import create_frozen, populate, recursive_program
from vendor.sdk2 import Program


def demo():
    with TemporaryDirectory(prefix="schema-warehouse-") as directory:
        path = Path(directory) / "warehouse.sqlite"
        bindings = create_frozen(path)
        with Warehouse(path) as warehouse:
            pending = warehouse.run(bindings["tasks"]["pending"])
            failure = warehouse.result(bindings["tasks"]["failure"])
            success = warehouse.result(bindings["tasks"]["success"])
            assert pending["state"] == "success" and len(pending["rows"]) == 4
            assert failure["error"]["row_index"] == 1 and failure["rows"] is None
            native = Program(recursive_program()).execute(warehouse.batches.get("good")["rows"][0])
            print(json.dumps({"fixed_read": pending, "enum_fallback": success,
                              "first_error": failure, "native_program": native},
                             ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Research schema warehouse")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo")
    seed = sub.add_parser("seed")
    seed.add_argument("database")
    sub.add_parser("probe")
    args = parser.parse_args()
    if args.command == "demo":
        demo()
    elif args.command == "seed":
        print(json.dumps(create_frozen(args.database), sort_keys=True))
    else:
        with Warehouse() as warehouse:
            bindings = populate(warehouse)
            result = warehouse.run(bindings["tasks"]["pending"])
            assert result["state"] == "success"
            assert len(result["rows"]) == 4
            print(json.dumps({"state": result["state"], "count": len(result["rows"])}))


if __name__ == "__main__":
    main()
