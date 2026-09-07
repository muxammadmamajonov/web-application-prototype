"""Import inputs are user errors, never unhandled server errors."""
import pytest
from fastapi import HTTPException

from app.api.tenant.imports import _parse_upload


@pytest.mark.parametrize(
    ("filename", "raw"),
    [
        ("renamed.xlsx", b"%PDF-1.7 not actually an xlsx"),
        ("empty.xlsx", b""),
    ],
)
def test_malformed_spreadsheet_is_a_bounded_400(filename: str, raw: bytes) -> None:
    with pytest.raises(HTTPException) as exc:
        _parse_upload(filename, raw)
    assert exc.value.status_code == 400
    assert exc.value.detail == "malformed_file"


def test_empty_csv_remains_a_specific_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _parse_upload("empty.csv", b"")
    assert exc.value.status_code == 400
    assert exc.value.detail == "empty_file"
