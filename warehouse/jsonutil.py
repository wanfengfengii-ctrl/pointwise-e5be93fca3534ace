import json
from .errors import PayloadError


def dumps(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PayloadError("value must be finite JSON") from exc


def loads(value):
    return json.loads(value)


def clone(value):
    return loads(dumps(value))
