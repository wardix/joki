import os, json, re, subprocess, socket
from joki.state import *
from joki.utils import *
from joki.display import _numbered
from joki.tools.files import *
from joki.tools.shell import *
from joki.tools.database import *
from joki.tools.memory import *
from joki.tools.security import *
from joki.tools.reverse_eng import *
from joki.tools.media import *
from joki.tools.ui import *
from joki.tools.web import *
from joki.tools.hardware import *
from joki.tools.lsp import handle_lsp_query
TOOL_HANDLERS = {
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "edit_file": handle_edit_file,
    "run_command": handle_run_command,
    "search_code": handle_search_code,
    "glob": handle_glob,
    "list_dir": handle_list_dir,
    "db_query": handle_db_query,
    "service_control": handle_service_control,
    "config_edit": handle_config_edit,
    "package_check": handle_package_check,
    "web_fetch": handle_web_fetch,
    "web_search": handle_web_search,
    "test_and_fix": handle_test_and_fix,
    "memory_store": handle_memory_store,
    "memory_recall": handle_memory_recall,
    "memory_forget": handle_memory_forget,
    "screenshot": handle_screenshot,
    "port_scan": handle_port_scan,
    "dns_enum": handle_dns_enum,
    "web_vuln_scan": handle_web_vuln_scan,
    "whois_lookup": handle_whois_lookup,
    "ssl_check": handle_ssl_check,
    "dir_bruteforce": handle_dir_bruteforce,
    "cve_search": handle_cve_search,
    "tech_detect": handle_tech_detect,
    "js_analyze": handle_js_analyze,
    "api_discover": handle_api_discover,
    "source_map_check": handle_source_map_check,
    "form_analyze": handle_form_analyze,
    "apk_analyze": handle_apk_analyze,
    "binary_analyze": handle_binary_analyze,
    "todo_create": handle_todo_create,
    "todo_done": handle_todo_done,
    "todo_show": handle_todo_show,
    "ui_screenshot": handle_ui_screenshot,
    "ui_click": handle_ui_click,
    "ui_type": handle_ui_type,
    "ui_keypress": handle_ui_keypress,
    "ui_focus": handle_ui_focus,
    "usb_list": handle_usb_list,
    "serial_send": handle_serial_send,
    "camera_capture": handle_camera_capture,
    "sandbox_run": handle_sandbox_run,
    "predict_command": handle_predict_command,
    "audio_info": handle_audio_info,
    "audio_transcribe": handle_audio_transcribe,
    "video_info": handle_video_info,
    "video_extract": handle_video_extract,
    "lsp_query": handle_lsp_query,
}

def execute(name, args):
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return f"Unknown tool: {name}"
    if not isinstance(args, dict):
        args = {}
    try:
        return handler(args)
    except KeyError as e:
        return f"Error: Missing required parameter '{e}' for tool '{name}'. LLM harus menyertakan parameter ini."
    except Exception as e:
        return f"Error: {e}"

# ============================================================
# LLM CALL
# ============================================================


