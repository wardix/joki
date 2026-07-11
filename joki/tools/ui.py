import os
import subprocess
import time


def handle_screenshot(args):
    path = args.get("path", f"/tmp/joki_screenshot_{int(time.time())}.png")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    cmds = [
        f"scrot '{path}'",
        f"import -window root '{path}'",
        f"gnome-screenshot -f '{path}'"
    ]
    for cmd in cmds:
        r = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            size = os.path.getsize(path)
            return f"Screenshot saved: {path} ({size} bytes)"
    return "Error: gagal mengambil screenshot. Install scrot: sudo apt install scrot"


def handle_ui_screenshot(args):
    path = args.get("path", "/tmp/joki_ui_screen.png")
    region = args.get("region", "full")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if region == "full":
        r = subprocess.run(["import", "-window", "root", path],
                           capture_output=True, text=True, timeout=15)
    else:
        r = subprocess.run(["import", "-crop", region, path],
                           capture_output=True, text=True, timeout=15)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return f"Screenshot saved: {path} ({os.path.getsize(path)} bytes)"
    return f"Error screenshot: {r.stderr or 'unknown'}. Install imagemagick: sudo apt install imagemagick"


def handle_ui_click(args):
    x = args.get("x")
    y = args.get("y")
    if x is None or y is None:
        return "Error: Parameter 'x' dan 'y' wajib diisi. Contoh: ui_click(x=500, y=300)"
    x, y = int(x), int(y)
    btn = args.get("button", "left")
    btn_map = {"left": 1, "middle": 2, "right": 3}
    count = args.get("click_count", 1)
    click_arg = "".join([str(btn_map.get(btn, 1))] * count)
    r = subprocess.run(["xdotool", "mousemove", str(x), str(
        y), "click", click_arg], capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        return f"Clicked {btn} at ({x},{y})"
    return f"Click error: {r.stderr}. Install xdotool: sudo apt install xdotool"


def handle_ui_type(args):
    text = args.get("text", "")
    if not text:
        return "Error: Parameter 'text' wajib diisi. Contoh: ui_type(text=\"Hello World\")"
    safe = text.replace('"', '\\"')
    r = subprocess.run(["xdotool", "type", safe],
                       capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        return f"Typed: {text[:100]}{'...' if len(text) > 100 else ''}"
    return f"Type error: {r.stderr}"


def handle_ui_keypress(args):
    keys = args.get("keys", "")
    if not keys:
        return "Error: Parameter 'keys' wajib diisi. Contoh: ui_keypress(keys=\"Return\")"
    r = subprocess.run(["xdotool", "key", keys],
                       capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        return f"Key pressed: {keys}"
    return f"Key error: {r.stderr}"


def handle_ui_focus(args):
    title = args.get("title", "")
    if not title:
        return "Error: Parameter 'title' wajib diisi. Contoh: ui_focus(title=\"Firefox\")"
    r = subprocess.run(["xdotool",
                        "search",
                        "--name",
                        title,
                        "windowactivate"],
                       capture_output=True,
                       text=True,
                       timeout=10)
    if r.returncode == 0 and r.stdout.strip():
        return f"Window focused: {title}"
    # fallback: coba windowactivate via classname
    r2 = subprocess.run(["xdotool",
                         "search",
                         "--class",
                         title,
                         "windowactivate"],
                        capture_output=True,
                        text=True,
                        timeout=10)
    if r2.returncode == 0 and r2.stdout.strip():
        return f"Window focused: {title}"
    return f"Window '{title}' not found. Gunakan --name atau --class."
