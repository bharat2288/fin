"""An uploaded file is saved inside the upload's temporary folder whatever
name the browser sends (pipeline #63): a name with "../" or an absolute path
never chooses where the file is written. The name sent is still what the
preview shows. Driven through the HTTP interface; the stand-in parser records
where each file was saved and reads nothing of it."""

import io
import os
import tempfile
from pathlib import Path

import parsers
from parse_dbs import ParsedStatement


def _upload(client, monkeypatch, tmp_path, names):
    """Upload one file per name; return the preview and the paths saved."""
    temp_root = tmp_path / "temp-root"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    seen = []

    def parse(path):
        seen.append(Path(path).resolve())
        return [ParsedStatement(statement_type="bank", statement_date="2026-08-31",
                                accounts=["Sample Bank 0002"], transactions=[])]

    parsers._PARSERS.insert(0, {
        "name": "Stand-in", "ext": ".csv", "detect_fn": lambda _path: True, "parse_fn": parse,
    })
    try:
        resp = client.post(
            "/api/import/upload",
            data={"files": [(io.BytesIO(b"stand-in"), name) for name in names]},
            content_type="multipart/form-data",
        )
    finally:
        parsers._PARSERS = [p for p in parsers._PARSERS if p["name"] != "Stand-in"]
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json(), seen, temp_root.resolve()


def test_a_name_climbing_out_of_the_folder_is_saved_inside_it(client, monkeypatch, tmp_path):
    preview, seen, temp_root = _upload(client, monkeypatch, tmp_path, ["../../escaped.csv"])

    (saved,) = seen
    assert temp_root in saved.parents
    assert not (tmp_path / "escaped.csv").exists()
    assert preview["filenames"] == ["../../escaped.csv"]


def test_an_absolute_name_is_saved_inside_the_folder(client, monkeypatch, tmp_path):
    target = tmp_path / "outside" / "planted.csv"
    target.parent.mkdir()

    preview, seen, temp_root = _upload(client, monkeypatch, tmp_path, [str(target)])

    (saved,) = seen
    assert temp_root in saved.parents
    assert not target.exists()


def test_two_files_whose_safe_names_collide_are_both_kept(client, monkeypatch, tmp_path):
    _, seen, temp_root = _upload(
        client, monkeypatch, tmp_path, ["a/statement.csv", "b/statement.csv", "..\\..\\statement.csv"],
    )

    assert len(seen) == 3 and len(set(seen)) == 3
    assert all(temp_root in path.parents for path in seen)
    assert all(path.suffix == ".csv" for path in seen)


def test_a_name_with_no_ascii_stem_keeps_its_extension(client, monkeypatch, tmp_path):
    preview, seen, temp_root = _upload(client, monkeypatch, tmp_path, ["выписка.csv", "отчёт за август.csv"])

    assert len(seen) == 2 and all(path.suffix == ".csv" for path in seen)
    assert all(temp_root in path.parents for path in seen)
    assert preview["filenames"] == ["выписка.csv", "отчёт за август.csv"]
