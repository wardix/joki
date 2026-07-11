import re
import os
import csv
import io
from joki.display import _console, stream_print
from rich.table import Table
from rich.syntax import Syntax

def print_tool_result_rich(name, args, result):
    if not result:
        return

    try:
        if name == "db_query":
            reader = csv.reader(io.StringIO(result), delimiter='\t')
            rows = list(reader)
            if rows and len(rows[0]) > 0:
                table = Table(title="Query Results")
                for col in rows[0]:
                    table.add_column(col)
                for row in rows[1:]:
                    table.add_row(*row)
                _console.print(table)
                return

        elif name == "read_file":
            path = args.get("path", "")
            ext = os.path.splitext(path)[1].lstrip(".").lower() or "text"
            _console.print(Syntax(result, ext, line_numbers=False, word_wrap=True))
            return

        elif name == "port_scan":
            lines = result.strip().splitlines()
            if len(lines) > 1 and "PORT" in lines[0].upper():
                table = Table(title="Port Scan Results")
                table.add_column("PORT")
                table.add_column("STATE")
                table.add_column("SERVICE")
                for line in lines:
                    parts = line.split(maxsplit=2)
                    if len(parts) >= 3 and not line.upper().startswith("PORT"):
                        table.add_row(parts[0], parts[1], parts[2])
                _console.print(table)
                return

        elif name == "search_code":
            _console.print(Syntax(result, "text", line_numbers=False, word_wrap=True, theme="monokai"))
            return

    except Exception:
        _console.print("[dim]Warning: Gagal menampilkan rich display, fallback ke plain text[/dim]")

    # Fallback to normal stream_print
    stream_print(f"       ```\n{result}\n       ```", delay=0.001)
