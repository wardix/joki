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


def _parse_connection(conn_str):
    """Parse connection string: mysql://..., postgres://..., mongodb://..., sqlite:///..."""
    sqlite_match = re.match(r"sqlite:///(.+)", conn_str)
    if sqlite_match:
        return ("sqlite", "", "", "", "", sqlite_match.group(1))

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
    scheme, user, password, host, port, database = _parse_connection(
        args["connection"])
    if not _confirm_dangerous(args["query"]):
        return "Dibatalkan oleh user."
    with _Spinner("Query database"):
        return _run_db_query(
            scheme,
            args["query"],
            user,
            password,
            host,
            port,
            database)
