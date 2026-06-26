import json
from decimal import Decimal
from poly import output


def test_to_jsonable_handles_decimal_and_pydantic():
    class Fake:
        def model_dump(self, mode="python"):
            return {"price": Decimal("0.5")}
    assert output.to_jsonable(Decimal("0.5")) == "0.5"
    assert output.to_jsonable([Fake()]) == [{"price": "0.5"}]


def test_table_columns_project_and_drop_others(capsys):
    rows = [{"a": "1", "b": "2", "huge": "x" * 90}]
    output.emit("table", rows, columns=["a", "b"])
    out = capsys.readouterr().out
    assert "a" in out and "b" in out
    assert "huge" not in out and "x" * 90 not in out


def test_json_ignores_columns_and_stays_full(capsys):
    output.emit("json", [{"a": "1", "huge": "keepme"}], columns=["a"])
    out = capsys.readouterr().out
    assert "huge" in out and "keepme" in out


def test_emit_json(capsys):
    output.emit("json", {"a": 1})
    assert json.loads(capsys.readouterr().out) == {"a": 1}


def test_emit_table_list(capsys):
    output.emit("table", [{"id": "1", "q": "x"}, {"id": "2", "q": "y"}])
    out = capsys.readouterr().out
    assert "id" in out and "q" in out and "x" in out


def test_print_error_json(capsys):
    output.print_error("json", "boom")
    assert json.loads(capsys.readouterr().out) == {"error": "boom"}


def test_print_error_table_stderr(capsys):
    output.print_error("table", "boom")
    captured = capsys.readouterr()
    assert "Error: boom" in captured.err
