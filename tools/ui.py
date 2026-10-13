import os
import re
import subprocess
import sys
import tempfile
import time


def _screenshot_mac(path):
    subprocess.run(["screencapture", "-x", path],
                   capture_output=True, text=True, timeout=15, check=False)
    return os.path.exists(path) and os.path.getsize(path) > 0


def _screenshot_linux(path):
    cmds = [
        f"scrot '{path}'",
        f"import -window root '{path}'",
        f"gnome-screenshot -f '{path}'"
    ]
    for cmd in cmds:
        subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15, check=False)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return True
    return False


def handle_screenshot(args):
    path = args.get("path", os.path.join(tempfile.gettempdir(), f"joki_screenshot_{int(time.time())}.png"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if sys.platform == 'darwin':
        ok = _screenshot_mac(path)
        hint = ""
    else:
        ok = _screenshot_linux(path)
        hint = "Install scrot: sudo apt install scrot || brew install scrot"
    if ok:
        return f"Screenshot saved: {path} ({os.path.getsize(path)} bytes)"
    return f"Error: gagal mengambil screenshot. {hint}"


def _ui_screenshot_mac(path, region):
    if region == "full" or region is None:
        subprocess.run(["screencapture", "-x", path],
                       capture_output=True, text=True, timeout=15, check=False)
    else:
        parts = region.split(",")
        if len(parts) == 4:
            x, y, w, h = map(int, parts)
            subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", path],
                           capture_output=True, text=True, timeout=15, check=False)
        else:
            subprocess.run(["screencapture", "-x", path],
                           capture_output=True, text=True, timeout=15, check=False)
    return os.path.exists(path) and os.path.getsize(path) > 0


def _ui_screenshot_linux(path, region):
    if region == "full":
        subprocess.run(["import", "-window", "root", path],
                       capture_output=True, text=True, timeout=15, check=False)
    else:
        subprocess.run(["import", "-crop", region, path],
                       capture_output=True, text=True, timeout=15, check=False)
    return os.path.exists(path) and os.path.getsize(path) > 0


def handle_ui_screenshot(args):
    path = args.get("path", os.path.join(tempfile.gettempdir(), "joki_ui_screen.png"))
    region = args.get("region", "full")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if sys.platform == 'darwin':
        ok = _ui_screenshot_mac(path, region)
        hint = ""
    else:
        ok = _ui_screenshot_linux(path, region)
        hint = "Install imagemagick: sudo apt install imagemagick"
    if ok:
        return f"Screenshot saved: {path} ({os.path.getsize(path)} bytes)"
    return f"Error screenshot: {hint}"


def _ui_action_mac(action, **kwargs):
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
    except ImportError:
        return "Install pyautogui: pip install pyautogui"

    try:
        if action == "click":
            x, y = int(kwargs["x"]), int(kwargs["y"])
            btn = kwargs.get("button", "left")
            count = kwargs.get("click_count", 1)
            pyautogui.click(x, y, button=btn, clicks=count)
            return f"Clicked {btn} at ({x},{y})"
        elif action == "type":
            pyautogui.typewrite(kwargs["text"], interval=0.05)
            text = kwargs["text"]
            return f"Typed: {text[:100]}{'...' if len(text) > 100 else ''}"
        elif action == "keypress":
            pyautogui.hotkey(*kwargs["keys"].split("+"))
            return f"Key pressed: {kwargs['keys']}"
        elif action == "focus":
            title = str(kwargs.get("title", ""))
            # Anti-injeksi: tolak shell meta (dulu os.system f-string).
            if not title or re.search(r'[;|&$`()<>\\\n]', title) or len(title) > 120:
                return "Error: title tidak valid (tolak shell meta, maks 120 char)."
            r = subprocess.run(
                ["osascript", "-e", f'tell application "{title}" to activate'],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if r.returncode == 0:
                return f"Window focused: {title}"
            return f"Error focus: {r.stderr[:200]}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def _ui_action_linux(action, **kwargs):
    if action == "click":
        try:
            x, y = int(kwargs["x"]), int(kwargs["y"])
        except (TypeError, ValueError):
            return "Error: x/y harus angka."
        btn = kwargs.get("button", "left")
        btn_map = {"left": 1, "middle": 2, "right": 3}
        try:
            count = max(1, min(int(kwargs.get("click_count", 1)), 10))
        except (TypeError, ValueError):
            return "Error: click_count harus 1-10."
        # Perbaikan bug "111": dulu join str button jadi "111" (invalid).
        # Sekarang pakai --repeat yang benar.
        r = subprocess.run(["xdotool", "mousemove", str(x), str(y), "click", "--repeat", str(count), str(btn_map.get(btn, 1))],
                           capture_output=True, text=True, timeout=10, check=False)
        if r.returncode == 0:
            return f"Clicked {btn} at ({x},{y})"
        return f"Click error: {r.stderr}. Install xdotool: sudo apt install xdotool"
    elif action == "type":
        text = str(kwargs.get("text", ""))
        if len(text) > 2000:
            return "Error: text maks 2000 char (pecah jadi beberapa ui_type)."
        # List-form tanpa shell: tidak perlu escape manual (dulu merusak kutip).
        r = subprocess.run(["xdotool", "type", "--", text],
                           capture_output=True, text=True, timeout=30, check=False)
        if r.returncode == 0:
            return f"Typed: {text[:100]}{'...' if len(text) > 100 else ''}"
        return f"Type error: {r.stderr}"
    elif action == "keypress":
        r = subprocess.run(["xdotool", "key", kwargs["keys"]],
                           capture_output=True, text=True, timeout=10, check=False)
        if r.returncode == 0:
            return f"Key pressed: {kwargs['keys']}"
        return f"Key error: {r.stderr}"
    elif action == "focus":
        title = kwargs["title"]
        r = subprocess.run(["xdotool", "search", "--name", title, "windowactivate"],
                           capture_output=True, text=True, timeout=10, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return f"Window focused: {title}"
        r2 = subprocess.run(["xdotool", "search", "--class", title, "windowactivate"],
                            capture_output=True, text=True, timeout=10, check=False)
        if r2.returncode == 0 and r2.stdout.strip():
            return f"Window focused: {title}"
        return f"Window '{title}' not found."


def handle_ui_click(args):
    x = args.get("x")
    y = args.get("y")
    if x is None or y is None:
        return "Error: Parameter 'x' dan 'y' wajib diisi. Contoh: ui_click(x=500, y=300)"
    if sys.platform == 'darwin':
        return _ui_action_mac("click", x=x, y=y, button=args.get("button", "left"), click_count=args.get("click_count", 1))
    return _ui_action_linux("click", x=x, y=y, button=args.get("button", "left"), click_count=args.get("click_count", 1))


def handle_ui_type(args):
    text = args.get("text", "")
    if not text:
        return "Error: Parameter 'text' wajib diisi. Contoh: ui_type(text=\"Hello World\")"
    if sys.platform == 'darwin':
        return _ui_action_mac("type", text=text)
    return _ui_action_linux("type", text=text)


def handle_ui_keypress(args):
    keys = args.get("keys", "")
    if not keys:
        return "Error: Parameter 'keys' wajib diisi. Contoh: ui_keypress(keys=\"Return\")"
    if sys.platform == 'darwin':
        return _ui_action_mac("keypress", keys=keys)
    return _ui_action_linux("keypress", keys=keys)


def handle_ui_focus(args):
    title = args.get("title", "")
    if not title:
        return "Error: Parameter 'title' wajib diisi. Contoh: ui_focus(title=\"Firefox\")"
    if sys.platform == 'darwin':
        return _ui_action_mac("focus", title=title)
    return _ui_action_linux("focus", title=title)
