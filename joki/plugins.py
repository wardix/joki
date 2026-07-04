import os
import sys
import glob
import importlib.util
from joki.constants import TOOLS
from joki.executor import TOOL_HANDLERS
from joki.config import _get_data_dir
from joki.display import stream_print

def _load_plugins():
    plugin_dir = os.path.join(_get_data_dir(), "plugins")
    os.makedirs(plugin_dir, exist_ok=True)
    
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)

    for f in glob.glob(os.path.join(plugin_dir, "*.py")):
        if os.path.basename(f) == "__init__.py":
            continue
            
        mod_name = f"joki_plugin_{os.path.splitext(os.path.basename(f))[0]}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, f)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
                
                if hasattr(mod, "TOOL_DEFINITION") and hasattr(mod, "handle"):
                    tool_def = mod.TOOL_DEFINITION
                    tool_name = tool_def["function"]["name"]
                    
                    # Prevent duplicates
                    for i, t in enumerate(TOOLS):
                        if t["function"]["name"] == tool_name:
                            TOOLS.pop(i)
                            break
                            
                    TOOLS.append(tool_def)
                    TOOL_HANDLERS[tool_name] = mod.handle
        except Exception as e:
            stream_print(f"[red]Gagal memuat plugin {f}: {e}[/red]\n")
