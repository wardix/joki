import os
import subprocess
import re
import httpx
from joki.state import _console


def handle_js_analyze(args):
    url = args.get("url", "").rstrip("/")
    if not url:
        return "Error: Parameter 'url' wajib diisi. Contoh: js_analyze(url=\"https://example.com\")"
    extract = args.get("extract", "all")
    output = []
    js_contents = []
    raw_js = ""

    if url.endswith(".js"):
        try:
            rr = httpx.get(
                url, timeout=15, verify=False, headers={
                    "User-Agent": "Mozilla/5.0"})
            if rr.status_code == 200:
                raw_js = rr.text
                js_contents.append((url.rsplit("/", 1)[-1], raw_js))
        except Exception:
            return f"[JS] Error fetching JS file: {url}"
    else:
        try:
            r = httpx.get(
                url,
                timeout=15,
                follow_redirects=True,
                verify=False,
                headers={
                    "User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                return f"[JS] Error: {url} returned {r.status_code}"
            scripts = re.findall(
                r'<script[^>]+src=["\']([^"\']+\.js[^"\']*)["\']',
                r.text,
                re.IGNORECASE)
            inline_scripts = re.findall(
                r'<script[^>]*>(.*?)</script>',
                r.text,
                re.IGNORECASE | re.DOTALL)
            inline_js = "\n".join(inline_scripts)
            if inline_js.strip():
                js_contents.append(("inline", inline_js))

            for src in scripts[:15]:
                js_url = src if src.startswith("http") else (
                    url.rstrip("/") + "/" + src.lstrip("/"))
                try:
                    rr = httpx.get(
                        js_url, timeout=10, verify=False, headers={
                            "User-Agent": "Mozilla/5.0"})
                    if rr.status_code == 200:
                        name = js_url.rsplit("/", 1)[-1][:40]
                        js_contents.append((name, rr.text))
                except Exception:
                    _console.print(f"[dim]Warning: Gagal fetch JS: {js_url}[/dim]")
        except Exception as e:
            return f"[JS] Error: {e}"

    if not js_contents:
        return f"[JS] No JavaScript found at {url}"

    output.append(f"  JS files analyzed: {len(js_contents)}")

    all_js = "\n".join(js for _, js in js_contents)

    if extract in ("endpoints", "all"):
        output.append(f"\n  [API Endpoints / URLs]")
        url_patterns = [
            r'["\'](https?://[^"\']+)["\']',
            r'["\'](/[a-zA-Z][^"\']*(?:api|v[0-9]+|rest|graphql|endpoint|webhook)[^"\']*)["\']',
            r'["\'](/[a-zA-Z][^"\']*/(?:get|post|put|delete|fetch|save|update|create|list|search|find|query)[^"\']*)["\']',
            r'["\'](/[a-zA-Z][^"\']*\.(?:php|asp|aspx|jsp|json|xml|do|action))["\']',
            r'fetch\(["\']([^"\']+)["\']',
            r'axios\.\w+\(["\']([^"\']+)["\']',
            r'ajax\(\s*["\']([^"\']+)["\']',
            r'\$\..*?\(["\']([^"\']+)["\']',
            r'XMLHttpRequest[^;]*["\']([^"\']+)["\']',
            r'url:\s*["\']([^"\']+)["\']',
            r'endpoint:\s*["\']([^"\']+)["\']',
            r'baseURL:\s*["\']([^"\']+)["\']',
            r'baseUrl:\s*["\']([^"\']+)["\']',
            r'apiUrl:\s*["\']([^"\']+)["\']',
            r'api_url:\s*["\']([^"\']+)["\']',
        ]
        found_urls = set()
        for pat in url_patterns:
            for m in re.finditer(pat, all_js, re.IGNORECASE):
                found_urls.add(m.group(1))

        found_urls = [u for u in found_urls if len(u) > 3 and u != " "]
        found_urls = sorted(set(found_urls))

        if found_urls:
            for u in found_urls[:40]:
                output.append(f"    {u}")
            if len(found_urls) > 40:
                output.append(f"    ... and {len(found_urls) - 40} more")
        else:
            output.append(f"    (no endpoints found)")

    if extract in ("secrets", "all"):
        output.append(f"\n  [Potential Secrets / Credentials]")
        secret_patterns = [
            (r'api[Kk]ey["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "API Key"),
            (r'api_key["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "API Key"),
            (r'apiSecret["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "API Secret"),
            (r'api_secret["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "API Secret"),
            (r'[Aa]ccess[Kk]ey["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "Access Key"),
            (r'[Ss]ecret[Aa]ccess[Kk]ey["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "Secret Access Key"),
            (r'[Aa]pp[Kk]ey["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "App Key"),
            (r'[Aa]pp[Ss]ecret["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "App Secret"),
            (r'[Tt]oken["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "Token"),
            (r'[Pp]assword["\']?\s*[:=]\s*["\']([^"\']{6,})["\']', "Password"),
            (r'[Pp]asswd["\']?\s*[:=]\s*["\']([^"\']{6,})["\']', "Password"),
            (r'[Ss]ecret["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "Secret"),
            (r'[Jj][Ww][Tt]["\']?\s*[:=]\s*["\']([^"\']+)["\']', "JWT"),
            (r'[Bb]earer\s+([a-zA-Z0-9._-]{20,})', "Bearer Token"),
            (r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----', "Private Key"),
            (r'ghp_[a-zA-Z0-9]{36}', "GitHub Token"),
            (r'sk_live_[a-zA-Z0-9]{24,}', "Stripe Live Key"),
            (r'pk_live_[a-zA-Z0-9]{24,}', "Stripe Live Key"),
            (r'sk_test_[a-zA-Z0-9]{24,}', "Stripe Test Key"),
            (r'AKIA[0-9A-Z]{16}', "AWS Access Key"),
            (r'["\']password["\'"]\s*["\']([^"\']{4,})["\']', "Hardcoded Password"),
            (r'["\'][Pp]assword["\']\s*[:=]\s*["\']([^"\']{4,})["\']', "Hardcoded Password"),
        ]
        secrets_found = []
        for pat, label in secret_patterns:
            matches = re.findall(pat, all_js)
            for m in matches:
                val = m if isinstance(m, str) else m[0]
                if val and len(val) < 200 and val not in (
                        "undefined", "null", "true", "false", ""):
                    secrets_found.append(f"    [{label}] {val[:80]}")

        if secrets_found:
            for s in secrets_found[:20]:
                output.append(s)
            if len(secrets_found) > 20:
                output.append(f"    ... and {len(secrets_found) - 20} more")
        else:
            output.append(f"    (no secrets detected)")

        output.append(f"\n  [Interesting Keywords]")
        keywords = [
            "debugger",
            "eval(",
            "Function(",
            "setTimeout",
            "setInterval",
            "XMLHttpRequest",
            "fetch(",
            "WebSocket",
            "localStorage",
            "sessionStorage",
            "document.cookie",
            "postMessage",
            "import(",
            "require(",
            "export ",
            "module.exports"]
        found_kw = []
        for kw in keywords:
            count = all_js.count(kw)
            if count > 0:
                found_kw.append(f"    {kw}: {count}x")
        if found_kw:
            output.extend(found_kw)
        else:
            output.append(f"    (none)")

    return f"[JS] JavaScript Analysis for {url}:\n" + "\n".join(output)


def handle_apk_analyze(args):
    path = args.get("path", "")
    output = []

    if not os.path.isfile(path):
        return f"[APK] File not found: {path}"

    size = os.path.getsize(path)
    output.append(f"  File: {path}")
    output.append(f"  Size: {size:,} bytes ({size/1024/1024:.1f} MB)")

    has_aapt = subprocess.run(
        ["which", "aapt2"], capture_output=True, text=True).returncode == 0
    has_aapt_old = subprocess.run(
        ["which", "aapt"], capture_output=True, text=True).returncode == 0
    has_apkanalyzer = subprocess.run(
        ["which", "apkanalyzer"], capture_output=True, text=True).returncode == 0
    has_unzip = subprocess.run(
        ["which", "unzip"], capture_output=True, text=True).returncode == 0
    has_jarsigner = subprocess.run(
        ["which", "jarsigner"], capture_output=True, text=True).returncode == 0

    if has_aapt:
        r = subprocess.run(["aapt2", "dump", "badging", path],
                           capture_output=True, text=True, timeout=60)
        out = r.stdout
        for line in out.splitlines():
            if any(
                k in line for k in [
                    "package:",
                    "application-label:",
                    "sdkVersion:",
                    "targetSdkVersion:",
                    "launchable-activity:",
                    "uses-permission:",
                    "uses-feature:",
                    "application-label-en:",
                    "versionCode:",
                    "versionName:",
                    "maxSdkVersion:",
                    "minSdkVersion:"]):
                output.append(f"  {line.strip()}")
    elif has_aapt_old:
        r = subprocess.run(["aapt", "dump", "badging", path],
                           capture_output=True, text=True, timeout=60)
        out = r.stdout
        for line in out.splitlines():
            if any(
                k in line for k in [
                    "package:",
                    "application-label:",
                    "sdkVersion:",
                    "targetSdkVersion:",
                    "launchable-activity:",
                    "uses-permission:",
                    "uses-feature:",
                    "application-label-en:",
                    "versionCode:",
                    "versionName:"]):
                output.append(f"  {line.strip()}")
    else:
        output.append(f"\n  [Basic Info (aapt2/aapt not installed)]")
        if has_unzip:
            r = subprocess.run(["unzip",
                                "-p",
                                path,
                                "AndroidManifest.xml"],
                               capture_output=True,
                               text=True,
                               timeout=30)
            if r.stdout:
                output.append(f"  AndroidManifest.xml extracted (binary)")
            r = subprocess.run(["unzip", "-l", path],
                               capture_output=True, text=True, timeout=30)
            for line in r.stdout.splitlines():
                if any(
                    k in line for k in [
                        ".dex",
                        "AndroidManifest",
                        "resources.arsc",
                        "lib/",
                        "META-INF",
                        "res/"]):
                    output.append(f"  {line.strip()}")

    if has_apkanalyzer:
        for info_type in ["manifest application-id", "manifest version-name",
                          "manifest version-code", "manifest min-sdk",
                          "manifest target-sdk", "manifest debuggable"]:
            r = subprocess.run(["apkanalyzer",
                                *info_type.split(),
                                path],
                               capture_output=True,
                               text=True,
                               timeout=30)
            if r.stdout.strip():
                output.append(f"  {info_type}: {r.stdout.strip()}")

    if has_jarsigner:
        r = subprocess.run(["jarsigner",
                            "-verify",
                            "-verbose",
                            "-certs",
                            path],
                           capture_output=True,
                           text=True,
                           timeout=30)
        for line in r.stderr.splitlines():
            if any(
                k in line for k in [
                    "jar verified",
                    "signer",
                    "X.509",
                    "CN="]):
                output.append(f"  [Sign] {line.strip()}")

    output.append(f"\n  [Available Analysis Tools]")
    tools_status = {
        "aapt2": has_aapt, "aapt": has_aapt_old,
        "apkanalyzer": has_apkanalyzer, "unzip": has_unzip,
        "jarsigner": has_jarsigner
    }
    for tool, available in tools_status.items():
        output.append(
            f"    {tool}: {'OKINSTALLED' if available else 'FAILNOT INSTALLED'}")
    output.append(f"\n  Install Android tools: sudo apt install android-sdk")
    output.append(f"  Install apkanalyzer: sudo apt install apkanalyzer")

    return f"[APK] APK Analysis:\n" + "\n".join(output)


def handle_binary_analyze(args):
    path = args.get("path", "")
    min_len = int(args.get("strings_min", 6))
    output = []

    if not os.path.isfile(path):
        return f"[BINARY] File not found: {path}"

    size = os.path.getsize(path)
    output.append(f"  File: {path}")
    output.append(f"  Size: {size:,} bytes ({size/1024/1024:.1f} MB)")

    has_file = subprocess.run(
        ["which", "file"], capture_output=True, text=True).returncode == 0
    has_strings = subprocess.run(
        ["which", "strings"], capture_output=True, text=True).returncode == 0
    has_objdump = subprocess.run(
        ["which", "objdump"], capture_output=True, text=True).returncode == 0
    has_xxd = subprocess.run(
        ["which", "xxd"], capture_output=True, text=True).returncode == 0
    has_exiftool = subprocess.run(
        ["which", "exiftool"], capture_output=True, text=True).returncode == 0

    if has_file:
        r = subprocess.run(["file", "-b", path],
                           capture_output=True, text=True, timeout=15)
        file_type = r.stdout.strip()
        output.append(f"  Type: {file_type}")
    else:
        output.append(f"  Type: (install 'file' command for detection)")

    if has_exiftool:
        r = subprocess.run(["exiftool", path],
                           capture_output=True, text=True, timeout=30)
        exif_lines = r.stdout.strip().splitlines()
        important_tags = ["File Size", "MIME Type", "Image Size", "File Type",
                          "Created Date", "Modify Date", "Create Date",
                          "Software", "Creator", "Author", "Producer",
                          "Application", "Company", "Architecture", "OS/ABI",
                          "Operating System", "Compiler", "Linker",
                          "Entry Point", "Section Count", "Debug Info",
                          "Machine", "Class", "Endianness"]
        for line in exif_lines:
            if any(t.lower() in line.lower() for t in important_tags):
                output.append(f"  [Meta] {line.strip()}")

    output.append(f"\n  [Available Tools]")
    tools_status = {
        "file": has_file, "strings": has_strings, "objdump": has_objdump,
        "xxd": has_xxd, "exiftool": has_exiftool
    }
    for tool, available in tools_status.items():
        output.append(
            f"    {tool}: {'OKINSTALLED' if available else 'FAILNOT INSTALLED'}")

    if has_objdump:
        output.append(f"\n  [ELF/Header Info]")
        r = subprocess.run(["objdump", "-f", path],
                           capture_output=True, text=True, timeout=15)
        header_info = r.stdout.strip()
        if header_info and "file format" in header_info:
            for line in header_info.splitlines()[:10]:
                if any(
                    k in line.lower() for k in [
                        "file format",
                        "architecture",
                        "flags",
                        "start address",
                        "entry"]):
                    output.append(f"    {line.strip()}")
        r2 = subprocess.run(["objdump", "-p", path],
                            capture_output=True, text=True, timeout=15)
        for line in r2.stdout.splitlines():
            if any(
                k in line.lower() for k in [
                    "needed",
                    "soname",
                    "rpath",
                    "runpath",
                    "interp",
                    "stack",
                    "relro",
                    "nx",
                    "pie",
                    "dynamic"]):
                output.append(f"    {line.strip()}")

    if has_strings:
        output.append(f"\n  [Strings (min {min_len} chars)]")
        r = subprocess.run(["strings",
                            f"-n{min_len}",
                            path],
                           capture_output=True,
                           text=True,
                           timeout=30)
        all_strings = r.stdout.splitlines()
        output.append(f"    Total strings: {len(all_strings)}")

        interesting_strings = []
        interesting_patterns = [
            r'https?://[^"\s]+', r'(?:sk|pk)_(?:live|test)_[a-zA-Z0-9]+',
            r'AKIA[0-9A-Z]{16}', r'-----BEGIN', r'password',
            r'api_key', r'secret', r'token', r'config',
            r'database', r'mysql', r'postgres', r'mongodb',
            r'/etc/', r'/var/', r'/home/', r'/tmp/',
            r'\.php', r'\.asp', r'\.jsp', r'\.exe',
            r'\.pdb', r'certificate', r'private_key',
        ]
        for s in all_strings:
            if len(s) < 4:
                continue
            for pat in interesting_patterns:
                if re.search(pat, s, re.IGNORECASE):
                    interesting_strings.append(s.strip())
                    break

        if interesting_strings:
            output.append(
                f"    Interesting strings: {len(interesting_strings)}")
            for s in sorted(set(interesting_strings))[:30]:
                output.append(f"      {s[:120]}")
        else:
            output.append(f"    (no interesting strings found)")

    return f"[BINARY] Binary Analysis:\n" + "\n".join(output)
