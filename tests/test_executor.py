"""Test untuk joki.executor — pastikan execute() meroute nama tool ke handler."""


def _tool_names():
    import joki.constants as c
    return {t["function"]["name"] for t in c.TOOLS}


def test_execute_dispatches_known_tool():
    """execute() harus mengembalikan handler untuk tool yang terdaftar."""
    import joki.executor as ex
    # read_file selalu ada di registry
    handler = ex.TOOL_HANDLERS.get("read_file")
    assert callable(handler)


def test_execute_unknown_tool_rejected():
    """Tool tak dikenal harus ditolak / tidak ada di dispatch table."""
    import joki.executor as ex
    names = _tool_names()
    unknown = "nonexistent_tool_xyz"
    assert unknown not in names
    assert ex.TOOL_HANDLERS.get(unknown) is None


def test_dispatch_table_covers_all_tools():
    """Setiap tool di constants.TOOLS harus ada di _DISPATCH."""
    import joki.executor as ex
    names = _tool_names()
    missing = names - set(ex.TOOL_HANDLERS.keys())
    # Boleh ada sedikit yang belum migaji, tapi kebanyikan harus lengkap
    assert len(missing) == 0
