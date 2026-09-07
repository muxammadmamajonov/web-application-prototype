"""PDF builders for the upload-guard suite.

Hand-built rather than library-generated so each fixture states exactly what is
and is not in the file: several cases exist to prove that a capability hidden
where a regex cannot see it — a compressed object stream, an escaped name — is
still caught.

Not a test module (no `test_` prefix), so pytest imports without collecting.
"""
from __future__ import annotations

import zlib


def _assemble(objects: list[bytes], root: int = 1) -> bytes:
    """A classic-xref PDF from 1-indexed object bodies."""
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {root} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def classic_pdf(pages: int = 1, *, catalog_extra: str = "", page_extra: str = "") -> bytes:
    """A valid PDF with a classic cross-reference table."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    objs = [
        f"<< /Type /Catalog /Pages 2 0 R {catalog_extra} >>".encode(),
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
    ]
    for _ in range(pages):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] {page_extra} >>".encode()
        )
    return _assemble(objs)


def bank_receipt_pdf() -> bytes:
    """An ordinary one-page slip with a font and a content stream — the shape a
    real bank produces, so "valid PDFs still work" is tested against something
    more than an empty page."""
    content = b"BT /F1 12 Tf 72 760 Td (To'lov cheki) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content
        + b"\nendstream",
    ]
    return _assemble(objs)


def objstm_pdf(pages: int = 1, *, catalog_extra: str = "") -> bytes:
    """A PDF whose catalog and page tree live in a COMPRESSED OBJECT STREAM,
    indexed by a cross-reference stream.

    This is the shape the previous regex guard could not read at all: it found
    no page count and no active-content marker because the bytes are deflated.
    """
    inner = [
        (1, f"<< /Type /Catalog /Pages 2 0 R {catalog_extra} >>".encode()),
        (2, f"<< /Type /Pages /Kids [{' '.join(f'{3 + i} 0 R' for i in range(pages))}] "
            f"/Count {pages} >>".encode()),
    ]
    for i in range(pages):
        inner.append((3 + i, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>"))

    pairs, bodies, offset = [], bytearray(), 0
    for num, body in inner:
        pairs.append(f"{num} {offset}")
        bodies += body + b" "
        offset = len(bodies)
    header = (" ".join(pairs) + "\n").encode()
    payload = zlib.compress(header + bytes(bodies))
    objstm_num = 3 + pages          # the ObjStm's own object number
    xref_num = objstm_num + 1

    out = bytearray(b"%PDF-1.5\n")
    objstm_off = len(out)
    out += (
        f"{objstm_num} 0 obj\n<< /Type /ObjStm /N {len(inner)} /First {len(header)} "
        f"/Length {len(payload)} /Filter /FlateDecode >>\nstream\n".encode()
        + payload + b"\nendstream\nendobj\n"
    )

    # A cross-reference stream: type-2 entries point compressed objects at the
    # ObjStm, type-1 entries give byte offsets for the two uncompressed ones.
    rows = bytearray()
    rows += bytes([0, 0, 0, 0, 0, 255, 255])                       # free head
    for idx in range(len(inner)):
        rows += bytes([2]) + objstm_num.to_bytes(4, "big") + idx.to_bytes(2, "big")
    rows += bytes([1]) + objstm_off.to_bytes(4, "big") + (0).to_bytes(2, "big")

    xref_off = len(out)
    rows += bytes([1]) + xref_off.to_bytes(4, "big") + (0).to_bytes(2, "big")
    xdata = zlib.compress(bytes(rows))
    out += (
        f"{xref_num} 0 obj\n<< /Type /XRef /Size {xref_num + 1} /W [1 4 2] "
        f"/Root 1 0 R /Filter /FlateDecode /Length {len(xdata)} >>\nstream\n".encode()
        + xdata + b"\nendstream\nendobj\n"
    )
    out += f"startxref\n{xref_off}\n%%EOF\n".encode()
    return bytes(out)


def encrypted_pdf() -> bytes:
    """A PDF declaring an /Encrypt dictionary in its trailer."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>",
        b"<< /Filter /Standard /V 1 /R 2 /O <" + b"41" * 32 + b"> /U <"
        + b"42" * 32 + b"> /P -44 >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Encrypt 4 0 R "
        f"/ID [<01> <02>] >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def malformed_xref_pdf() -> bytes:
    """Header and objects present, cross-reference table pointing nowhere."""
    body = classic_pdf()
    return body.replace(b"startxref", b"startxrEf", 1)[: -len(b"%%EOF\n")] + b"%%EOF\n"
