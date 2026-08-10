from __future__ import annotations

import hashlib
import io

import pytest
from openpyxl import Workbook

from order_pipeline.attachments import (
    AttachmentBoundaryError,
    DeterministicAttachmentScanner,
    scan_and_parse_attachment,
)


def test_xlsx_formula_without_cached_value_is_explicit_partial_parse(tmp_path):
    del tmp_path
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet["A1"] = "SKU"
    sheet["A2"] = "STL-BEAM-200"
    sheet["B1"] = "Quantity"
    sheet["B2"] = "=5+5"
    buffer = io.BytesIO()
    workbook.save(buffer)
    content = buffer.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    with pytest.raises(AttachmentBoundaryError) as error:
        scan_and_parse_attachment(
            attachment_id="formula-xlsx",
            content=content,
            original_filename="formula.xlsx",
            declared_media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            expected_digest=digest,
            scanner=DeterministicAttachmentScanner(),
        )
    assert error.value.code == "XLSX_FORMULA_CACHE_MISSING"
