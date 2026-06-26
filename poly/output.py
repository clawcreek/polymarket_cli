"""Output formatting: one place for json vs table rendering and errors."""

import json
import sys
from decimal import Decimal
from typing import Any


def to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return to_jsonable(obj.model_dump(mode="json"))
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def _render_table(data: Any, columns: list[str] | None = None) -> str:
    data = to_jsonable(data)
    if isinstance(data, list):
        if not data:
            return "(no results)"
        if columns and all(isinstance(row, dict) for row in data):
            cols = list(columns)
        else:
            cols = list({k: None for row in data for k in (row if isinstance(row, dict) else {})})
        widths = {c: max(len(c), *(len(_cell(r.get(c))) for r in data)) for c in cols}
        header = "  ".join(c.ljust(widths[c]) for c in cols)
        rows = ["  ".join(_cell(r.get(c)).ljust(widths[c]) for c in cols) for r in data]
        return "\n".join([header, *rows])
    if isinstance(data, dict):
        w = max((len(k) for k in data), default=0)
        return "\n".join(f"{k.ljust(w)}  {_cell(v)}" for k, v in data.items())
    return str(data)


def emit(fmt: str, data: Any, columns: list[str] | None = None) -> None:
    """Print ``data``. ``columns`` selects which fields show in TABLE mode only;
    JSON always emits the full object."""
    if fmt == "json":
        print(json.dumps(to_jsonable(data), indent=2))
    else:
        print(_render_table(data, columns))


def print_error(fmt: str, message: str) -> None:
    if fmt == "json":
        print(json.dumps({"error": message}))
    else:
        print(f"Error: {message}", file=sys.stderr)
