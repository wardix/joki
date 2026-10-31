import sys
import types

from joki.executor import execute


def test_predict_command_dangerous():
    result = execute("predict_command", {"cmd": "rm -rf /"})
    assert "⚠" in result

def test_predict_command_safe():
    result = execute("predict_command", {"cmd": "ls -la"})
    assert "⚠" not in result


def test_serial_send_invalid_port_rejected(monkeypatch):
    from joki.tools.hardware import handle_serial_send
    monkeypatch.setattr("joki.tools.hardware._list_serial_ports", lambda: set())
    result = handle_serial_send({"port": "/dev/ttyNONEXISTENT123", "data": "hello"})
    assert "tidak terdeteksi" in result


def test_serial_send_valid_port_calls_serial(monkeypatch):
    from joki.tools.hardware import handle_serial_send
    sent = {}
    class FakeSerial:
        def __init__(self, port, baud, timeout=2):
            sent["port"] = port
            sent["baud"] = baud
        def write(self, data):
            sent["data"] = data
        def close(self):
            pass
        in_waiting = 0
    fake_mod = types.ModuleType("serial")
    fake_mod.Serial = FakeSerial  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "serial", fake_mod)
    monkeypatch.setattr(
        "joki.tools.hardware._list_serial_ports",
        lambda: {"/dev/ttyUSB0"})
    result = handle_serial_send({
        "port": "/dev/ttyUSB0", "data": "hello",
        "baud": 9600, "read_timeout": 0.1})
    assert "Sent" in result
    assert sent["data"] == b"hello"
