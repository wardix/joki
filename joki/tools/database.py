import os
import subprocess
import re
from joki.state import *
from joki.utils import *
from joki.display import _Spinner


def _parse_connection(conn_str):
    """Parse connection string: mysql://..., postgres://..., mongodb://..., sqlite:///..."""
    sqlite_match = re.match(r"sqlite:///(.+)", conn_str)
    if sqlite_match:
        path = sqlite_match.group(1)
        if not path.startswith("/"):
            path = "/" + path
        return ("sqlite", "", "", "", "", path)

    match = re.match(
        r"(\w+)://(?:([^:@]+)(?::([^@]+))?@)?([^:/]+)(?::(\d+))?(?:/(.+))?",
        conn_str)
    if not match:
        raise ValueError(f"Invalid connection string: {conn_str}")
    scheme, user, password, host, port, database = match.groups()
    return scheme, user or "root", password or "", host or "localhost", port, database or ""


def _run_db_query(scheme, query, user, password, host, port, database):
    scheme = scheme.lower()

    if scheme in ("mysql", "mariadb"):
        env = os.environ.copy()
        if password:
            env["MYSQL_PWD"] = password
        cmd = ["mysql", f"-u{user}"]
        if password:
            cmd.append(f"-p{password}")
        cmd.extend([f"-h{host}", f"-P{port or 3306}", database, "-e", query])
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=env)
        return r.stdout or r.stderr

    elif scheme in ("postgres", "postgresql", "pgsql"):
        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password
        cmd = [
            "psql",
            f"-h{host}",
            f"-p{port or 5432}",
            f"-U{user}",
            f"-d{database}",
            "-c",
            query,
            "-t"]
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=env)
        return r.stdout or r.stderr

    elif scheme == "mongodb":
        cmd = ["mongosh", f"{host}:{port or 27017}/{database}"]
        if user:
            cmd.extend(["-u", user, "-p", password,
                       "--authenticationDatabase", "admin"])
        cmd.extend(["--quiet", "--eval", query])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr

    elif scheme == "sqlite":
        if not os.path.isfile(database):
            database = os.path.expanduser(database)
        cmd = ["sqlite3", database, query]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr

    elif scheme in ("mssql", "sqlserver"):
        cmd = ["sqlcmd", "-S", f"{host},{port or 1433}", "-U", user]
        if password:
            cmd.extend(["-P", password])
        else:
            cmd.append("-E")
        cmd.extend(["-d", database, "-Q", query, "-W"])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr

    elif scheme in ("oracle", "oracledb"):
        pw_part = f"{user}/{password}@" if password else f"{user}/"
        cmd = ["sqlplus", "-S", f"{pw_part}{host}:{port or 1521}/{database}"]
        r = subprocess.run(
            cmd,
            input=query,
            capture_output=True,
            text=True,
            timeout=30)
        return r.stdout or r.stderr

    elif scheme == "redis":
        cmd = ["redis-cli", "-h", host, "-p", str(port or 6379)]
        if password:
            cmd.extend(["-a", password])
        cmd.extend(shlex.split(query))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr

    else:
        dbs = "mysql, postgres, mongodb, sqlite, mssql, oracle, redis"
        return f"Unsupported database: {scheme}. Supported: {dbs}."

# ============================================================
# LONG-TERM MEMORY (per-session)
# ============================================================


def handle_db_query(args):
    conn = args.get("connection", "")
    query = args.get("query", "")
    if not conn:
        return "Error: Parameter 'connection' wajib diisi. Contoh: db_query(connection=\"sqlite:///data.db\", query=\"SELECT * FROM users\")"
    if not query:
        return "Error: Parameter 'query' wajib diisi. Contoh: db_query(connection=\"sqlite:///data.db\", query=\"SELECT * FROM users\")"
    try:
        scheme, user, password, host, port, database = _parse_connection(conn)
    except ValueError as e:
        return f"Error: {e}. Format yang benar: mysql://user:pass@host:port/db, sqlite:///path, postgres://..., mongodb://..., mssql://..., oracle://..., redis://..."
    with _Spinner("Query database"):
        return _run_db_query(
            scheme,
            query,
            user,
            password,
            host,
            port,
            database)
