import threading
from rich.console import Console

__version__ = "0.1.0"
BACKUP_DIR = "/tmp/agent_backups"

try:
    import termios, tty
    _HAS_TTY = True
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

class JokiError(Exception): pass
class ToolError(JokiError): pass
class LLMError(JokiError): pass
class ConfigError(JokiError): pass
