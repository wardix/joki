import os
import shutil
import tempfile

from joki.tools.office import handle_read_office, handle_write_office


def test_write_read_csv_roundtrip():
    tmpdir = tempfile.mkdtemp(prefix="joki_test_office_")
    try:
        path = os.path.join(tmpdir, "test.csv")
        result = handle_write_office({
            "path": path,
            "content": "a,b,c\n1,2,3\n4,5,6\n"
        })
        assert "created" in (result or "").lower()

        content = handle_read_office({"path": path})
        assert content is not None
        assert "a" in content or "1" in content
    finally:
        shutil.rmtree(tmpdir)


def test_read_office_file_not_found():
    result = handle_read_office({"path": "/nonexistent/file.docx"})
    assert "tidak ditemukan" in (result or "") or "not found" in (result or "").lower()


def test_write_office_unsupported_extension():
    tmpdir = tempfile.mkdtemp(prefix="joki_test_office_")
    try:
        path = os.path.join(tmpdir, "test.xyz")
        result = handle_write_office({
            "path": path,
            "content": "test"
        })
        assert "tidak didukung" in (result or "")
    finally:
        shutil.rmtree(tmpdir)
