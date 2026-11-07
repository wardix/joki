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


def test_execute_shell_alias_maps_to_run_command():
    """Regresi: model sering menghalusinasikan tool 'shell' — harus jadi run_command."""
    import joki.executor as ex
    result = ex.execute("shell", {"cmd": "echo alias_shell_ok"})
    assert "Unknown tool" not in result
    assert "alias_shell_ok" in result


def test_execute_shell_alias_accepts_alt_command_key():
    """Model kadang mengirim argumen 'command'/'script' alih-alih 'cmd'."""
    import joki.executor as ex
    result = ex.execute("shell", {"command": "echo alias_arg_ok"})
    assert "alias_arg_ok" in result
    result = ex.execute("bash", {"script": "echo alias_script_ok"})
    assert "alias_script_ok" in result


def test_execute_alias_respects_safety_gate():
    """Alias tidak boleh menembus gate: edit via sed tetap diblokir."""
    import joki.executor as ex
    result = ex.execute("shell", {"cmd": "sed -i 's/a/b/g' /tmp/x.txt"})
    assert "BLOKIR" in result


def test_execute_truly_unknown_tool_has_actionable_message():
    import joki.executor as ex
    result = ex.execute("nonexistent_tool_xyz", {})
    assert result.startswith("Unknown tool:")
    assert "run_command" in result


def test_normalize_tool_name_public_wrapper():
    """cli memakai normalisasi ini saat parse tool_calls (display + confirm + loop)."""
    import joki.executor as ex
    assert ex.normalize_tool_name("shell") == "run_command"
    assert ex.normalize_tool_name("BASH") == "run_command"
    assert ex.normalize_tool_name(" run_command ") == "run_command"
    assert ex.normalize_tool_name("read_file") == "read_file"
    assert ex.normalize_tool_name("tool_ngawur") == "tool_ngawur"
