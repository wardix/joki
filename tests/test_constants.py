"""Test dasar untuk joki.constants — pastikan TOOLS & konstanta krusial tersedia."""

import pytest


def test_tools_registry_is_list():
    import joki.constants as c
    assert isinstance(c.TOOLS, list)
    assert len(c.TOOLS) > 0


@pytest.mark.parametrize("tool_entry", [
    "read_file", "write_file", "edit_file",
    "run_command", "search_code",
    "web_search", "web_fetch",
    "git_status", "git_commit",
    "memory_store", "memory_recall", "todo_create",
])
def test_expected_tool_names_present(tool_entry):
    """Setiap tool penting harus ada di registry TOOLS."""
    import joki.constants as c
    names = {t["function"]["name"] for t in c.TOOLS}
    assert tool_entry in names


def test_max_tokens_is_int():
    import joki.constants as c
    assert isinstance(c.MAX_TOKENS, int)
    assert c.MAX_TOKENS > 0


def test_mode_constants_unique():
    import joki.constants as c
    modes = {c.MODE_CODE, c.MODE_SYSADMIN, c.MODE_SECURITY, c.MODE_GENERAL}
    assert len(modes) >= 3  # minimal 3 mode berbeda
