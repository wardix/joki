import os
import sys
import json
import subprocess
import sqlite3
import re
import time
import random
import base64
import socket
import urllib
import csv
import platform
import ssl
from pathlib import Path
from difflib import unified_diff
from datetime import datetime
import httpx
from duckduckgo_search import DDGS
from joki.state import *
from joki.utils import *
from joki.display import _numbered, _Spinner


def handle_usb_list(args):
    verbose = args.get("verbose", False)
    cmd = ["lsusb"] if not verbose else ["lsusb", "-v"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        out = r.stdout.strip()
        return out or "(no USB devices)"
    return f"lsusb error: {r.stderr}. Install usbutils: sudo apt install usbutils"


def handle_serial_send(args):
    port = args["port"]
    data = args["data"]
    baud = str(args.get("baud", 9600))
    timeout = args.get("read_timeout", 2)
    try:
        import serial
    except ImportError:
        return "pyserial tidak terinstall. Install: pip install pyserial"
    try:
        ser = serial.Serial(port, int(baud), timeout=timeout)
        ser.write(data.encode())
        response = b""
        import time as _time
        _time.sleep(0.5)
        while ser.in_waiting:
            response += ser.read(ser.in_waiting)
            _time.sleep(0.2)
        ser.close()
        resp_text = response.decode(errors="replace").strip()
        if resp_text:
            return f"Sent: {data}\nResponse: {resp_text}"
        return f"Sent: {data} (no response)"
    except Exception as e:
        return f"Serial error: {e}"
