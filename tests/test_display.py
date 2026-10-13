from joki import state
from joki.display import _message_card, _numbered, stream_print
from joki.state import _SideCard


def test_numbered_single_line():
    assert _numbered("hello") == "1: hello"

def test_numbered_multiple_lines():
    text = "line1\nline2\nline3"
    expected = "1: line1\n2: line2\n3: line3"
    assert _numbered(text) == expected

def test_numbered_empty():
    assert _numbered("") == "1: "


def test_message_card_is_side_card():
    card = _message_card("isi", "USER")
    assert isinstance(card, _SideCard)
    assert card.border_style == "blue"
    assert card.title == "USER"
    assert card.body == "isi"


def test_message_card_white_on_light_theme(monkeypatch):
    monkeypatch.setattr(state, "_THEME_DARK", False)
    card = _message_card("isi", "USER")
    assert card.style == "on white"

    monkeypatch.setattr(state, "_THEME_DARK", True)
    card = _message_card("isi", "USER")
    assert card.style is None


def test_stream_print_with_card_routes_to_console(monkeypatch):
    captured = []

    class _FakeConsole:
        def print(self, *objects, **kwargs):
            captured.append((objects, kwargs))

    monkeypatch.setattr(state, "_TUI_ACTIVE", False)
    monkeypatch.setattr("joki.display._console", _FakeConsole())
    stream_print("halo dunia", card="[bold cyan]JOKI[/bold cyan]")
    assert len(captured) == 1
    obj = captured[0][0][0]
    assert isinstance(obj, _SideCard)
    assert obj.title == "[bold cyan]JOKI[/bold cyan]"
    assert obj.border_style == "blue"


def test_stream_print_without_card_prints_raw(monkeypatch):
    captured = []

    class _FakeConsole:
        def print(self, *objects, **kwargs):
            captured.append((objects, kwargs))

    monkeypatch.setattr(state, "_TUI_ACTIVE", False)
    monkeypatch.setattr("joki.display._console", _FakeConsole())
    stream_print("teks biasa")
    assert len(captured) == 1
    assert not isinstance(captured[0][0][0], _SideCard)
