import inspect


def _get_default_verify(func):
    sig = inspect.signature(func)
    for name in sig.parameters:
        if name == "args":
            continue


def _find_httpx_get_calls(func):
    """Cari semua httpx.get/post/head calls dan cek default verify."""
    import ast
    try:
        source = inspect.getsource(func)
    except Exception:  # noqa: BLE001
        return []
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "post", "head") and isinstance(node.func.value, ast.Name) and node.func.value.id == "httpx":
            for kw in node.keywords:
                if kw.arg == "verify" and isinstance(kw.value, ast.Constant):
                    calls.append((node.func.attr, kw.value.value))
    return calls


def test_web_make_session_client_verify_true():
    import inspect

    from joki.tools.web import _make_session_client
    source = inspect.getsource(_make_session_client)
    assert "verify=True" in source
    assert "verify=False" not in source


def test_reverse_eng_js_analyze_has_skip_ssl_param():
    from joki.tools.reverse_eng import handle_js_analyze
    source = inspect.getsource(handle_js_analyze)
    assert "skip_ssl_verify" in source
    assert "verify=_verify" in source


def test_security_handlers_have_http_verify():
    from joki.tools.security import _http_verify
    assert _http_verify({"skip_ssl_verify": False}) is True
    assert _http_verify({"skip_ssl_verify": True}) is False
    assert _http_verify({}) is True  # default
