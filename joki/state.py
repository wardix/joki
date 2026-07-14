import sys, threading
from rich.console import Console

__version__ = "0.1.0"
__all__ = [
    "__version__", "BACKUP_DIR", "_HAS_TTY", "_IS_TTY",
    "_console", "_current_model_config", "_CURRENT_SESSION",
    "_joki_cancel", "_exhausted_keys", "_SUDO_PASSWORD",
    "_PERSISTENT_SHELL", "_SHELL_LOCK", "_PTY_SESSION", "_PTY_LOCK",
    "_READ_FILES",
    "JokiError", "ToolError", "LLMError", "ConfigError",
]
BACKUP_DIR = "/tmp/agent_backups"

_IS_TTY = sys.stdout.isatty()

try:
    import termios, tty
    _HAS_TTY = _IS_TTY
except ImportError:
    _HAS_TTY = False

_console = Console()
_current_model_config = {}
_CURRENT_SESSION = None
_joki_cancel = threading.Event()
_exhausted_keys = set()
_SUDO_PASSWORD = None
_PERSISTENT_SHELL = None
_SHELL_LOCK = threading.Lock()
_PTY_SESSION = None
_PTY_LOCK = threading.Lock()
_READ_FILES = set()
_CURRENT_SPINNER = None
_LSP_CLIENTS = {}
_LSP_LOCK = threading.Lock()

class JokiError(Exception): pass
class ToolError(JokiError): pass
class LLMError(JokiError): pass
class ConfigError(JokiError): pass
