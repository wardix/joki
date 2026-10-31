import json
import os
import random
import re
import time
import urllib.parse
from html import unescape
from typing import Any

import httpx

from joki.config import _CONFIG_PATH
from joki.display import _Spinner

try:
    import html2text
    _HAS_HTML2TEXT = True
except ImportError:
    _HAS_HTML2TEXT = False

try:
    import cloudscraper
    _HAS_CLOUDSCRAPER = True
except ImportError:
    _HAS_CLOUDSCRAPER = False

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Dnt": "1",
}

_LAST_REQUEST_TIME = 0

def _random_ua():
    return random.choice(_USER_AGENTS)

def _build_headers():
    h = dict(_BROWSER_HEADERS)
    h["User-Agent"] = _random_ua()
    return h

def _respect_rate_limit():
    global _LAST_REQUEST_TIME
    now = time.time()
    since_last = now - _LAST_REQUEST_TIME
    if since_last < 1.0:
        time.sleep(max(0, random.uniform(0.3, 1.0) - since_last))
    _LAST_REQUEST_TIME = time.time()

# ── Session store untuk autentikasi website ──
_web_sessions: dict[str, Any] = {}


def _domain_from_url(url):
    return urllib.parse.urlparse(url).netloc.split(":")[0]


def _get_session(domain):
    return _web_sessions.get(domain)


def _set_session(domain, client):
    _web_sessions[domain] = client


def _del_session(domain):
    _web_sessions.pop(domain, None)


def _make_session_client():
    return httpx.Client(
        follow_redirects=True,
        verify=True,
        headers=_build_headers(),
    )


def _get_config_key(key_name):
    val = os.environ.get(key_name.upper()) or os.environ.get(f"JOKI_{key_name.upper()}")
    if val:
        return val
    try:
        data = json.loads(_CONFIG_PATH.read_text())
        return data.get(key_name, "") or ""
    except Exception:  # noqa: BLE001
        return ""


def _get_tinyfish_key():
    return _get_config_key("tinyfish_api_key")


def _get_brave_key():
    val = os.environ.get("BRAVE_API_KEY") or os.environ.get("JOKI_BRAVE_KEY")
    if val:
        return val
    try:
        data = json.loads(_CONFIG_PATH.read_text())
        return data.get("brave_api_key", "") or ""
    except Exception:  # noqa: BLE001
        return ""


def _fetch_httpx(url, timeout=30, params=None, domain=None):
    _respect_rate_limit()

    client = _get_session(domain) if domain else None
    if client:
        return client.get(url, params=params, timeout=timeout)

    # Coba cloudscraper dulu kalo tersedia (bypass Cloudflare)
    if _HAS_CLOUDSCRAPER:
        try:
            scraper = cloudscraper.create_scraper()
            r = scraper.get(url, params=params, timeout=timeout, headers=_build_headers())
            return httpx.Response(
                status_code=r.status_code,
                headers=dict(r.headers),
                content=r.content,
                request=httpx.Request("GET", url),
            )
        except Exception:  # noqa: BLE001, S110
            pass

    return httpx.get(
        url, params=params, timeout=timeout, follow_redirects=True, verify=True,
        headers=_build_headers(),
    )


def _fetch_via_tinyfish(url):
    key = _get_tinyfish_key()
    if not key:
        return None
    try:
        r = httpx.post(
            "https://api.fetch.tinyfish.ai",
            json={"urls": [url], "format": "markdown"},
            headers={"X-API-Key": key, "Content-Type": "application/json"},
            verify=True,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or data
        if isinstance(results, list) and results:
            md = results[0].get("markdown", "") or results[0].get("content", "")
            if md and md.strip():
                return md.strip()
        return None
    except Exception:  # noqa: BLE001
        return None


def handle_web_fetch(args):
    url = args.get("url", "")
    force_text = args.get("format", "markdown") == "text"
    if not url:
        return "Error: URL wajib diisi. Contoh: web_fetch(url=\"https://example.com\")"

    with _Spinner("Mengambil konten web"):
        direct_result = None
        fetch_error = None

        # Priority 1: Direct httpx + html2text (cepat, tanpa API eksternal)
        try:
            r = _fetch_httpx(url)
            r.raise_for_status()
            content_type = r.headers.get("content-type", "")
            if "json" in content_type:
                try:
                    return json.dumps(r.json(), indent=2)
                except Exception:  # noqa: BLE001, S110
                    pass
            if force_text or not _HAS_HTML2TEXT or not r.text.strip():
                direct_result = r.text or "(konten kosong)"
            else:
                h = html2text.HTML2Text()
                h.body_width = 0
                h.ignore_links = False
                h.ignore_images = False
                h.ignore_emphasis = False
                h.skip_internal_links = True
                h.protect_links = True
                md = h.handle(r.text)
                direct_result = md.strip() or "(konten kosong)"
        except httpx.TimeoutException:
            fetch_error = "Timeout (30 detik)"
        except httpx.HTTPStatusError as e:
            fetch_error = f"HTTP {e.response.status_code}"
        except httpx.InvalidURL:
            return f"Error: URL tidak valid: {url}"
        except Exception as e:  # noqa: BLE001
            fetch_error = str(e)[:100]

        # Priority 2: TinyFish API sebagai fallback kalo direct gagal
        if not direct_result and not force_text:
            md = _fetch_via_tinyfish(url)
            if md:
                return md

        if direct_result:
            return direct_result

        if fetch_error:
            return f"Error: Gagal mengambil {url}: {fetch_error}"
        return f"Error: Gagal mengambil {url}"


def handle_web_scrape(args):
    url = args.get("url", "")
    topic = args.get("topic", "")
    selector = args.get("selector", "")
    use_session = args.get("use_session", False)

    if not url:
        return "Error: URL wajib diisi. Contoh: web_scrape(url=\"https://detik.com\")"

    with _Spinner(f"Mengambil data dari {url}"):
        try:
            domain = _domain_from_url(url)
            downloaded = None

            if use_session:
                client = _get_session(domain)
                if not client:
                    return f"Belum login ke {domain}. Login dulu dengan web_login(url='{domain}', ...)"
                try:
                    r = client.get(url, timeout=30)
                    r.raise_for_status()
                    downloaded = r.text
                except Exception as e:  # noqa: BLE001
                    return f"Error mengambil {url} dengan session: {e}"
            else:
                # Priority 1: trafilatura (extraction quality terbaik)
                try:
                    import trafilatura
                    downloaded = trafilatura.fetch_url(url)
                except ImportError:
                    downloaded = None

                # Priority 2: Fallback ke httpx langsung kalo trafilatura gagal
                if not downloaded:
                    try:
                        r = _fetch_httpx(url)
                        r.raise_for_status()
                        downloaded = r.text
                    except Exception:  # noqa: BLE001
                        return f"Gagal mengunduh konten dari {url}. Website mungkin memblokir akses."

            if not downloaded:
                return f"Gagal mengunduh konten dari {url}. Website mungkin memblokir akses."

            if selector:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(downloaded, "lxml")
                elements = soup.select(selector)
                if not elements:
                    return f"Tidak ada elemen yang cocok dengan selector '{selector}'"
                text = "\n\n".join(el.get_text(strip=True) for el in elements if el.get_text(strip=True))
                result = text if text else "(konten kosong)"
            else:
                result = None
                try:
                    import trafilatura
                    result = trafilatura.extract(
                        downloaded,
                        include_links=True,
                        include_images=False,
                        include_tables=True,
                        output_format="markdown",
                        favor_precision=True,
                    )
                except ImportError:
                    pass

                if not result or not result.strip():
                    try:
                        import trafilatura
                        result = trafilatura.extract(
                            downloaded,
                            include_links=False,
                            output_format="txt",
                            favor_recall=True,
                        )
                    except ImportError:
                        pass

                if not result or not result.strip():
                    if _HAS_HTML2TEXT:
                        h = html2text.HTML2Text()
                        h.body_width = 0
                        h.ignore_links = False
                        h.ignore_images = True
                        h.ignore_emphasis = False
                        result = h.handle(downloaded).strip()
                    else:
                        result = "(tidak ada konten yang bisa diekstrak)"

            if topic:
                result = _filter_by_topic(result, topic)

            output = f"**Sumber:** {url}\n"
            if topic:
                output += f"**Topik:** {topic}\n"
            if use_session:
                output += f"**Session:** {domain} (authenticated)\n"
            output += f"**Karakter:** {len(result)}\n"
            output += f"\n---\n\n{result}"
            return output

        except Exception as e:  # noqa: BLE001
            return f"Error scraping {url}: {e}"


def handle_web_login(args):
    url = args.get("url", "")
    username = args.get("username", "")
    password = args.get("password", "")
    username_field = args.get("username_field", "")
    password_field = args.get("password_field", "")

    if not url or not username or not password:
        return "Error: Parameter 'url', 'username', dan 'password' wajib diisi."

    domain = _domain_from_url(url)
    with _Spinner(f"Mencoba login ke {domain}"):
        try:
            client = _make_session_client()
            from bs4 import BeautifulSoup

            # 1. GET login page
            r = client.get(url, timeout=30)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            # 2. Cari form login
            form = soup.find("form")
            if not form:
                return f"Tidak menemukan form login di {url}."

            # 3. Kumpulin semua input di form
            inputs = form.find_all("input")
            form_action = form.get("action", url)
            if not form_action.startswith("http"):
                form_action = urllib.parse.urljoin(url, form_action)

            form_data = {}
            detected_user_field = ""
            detected_pass_field = ""
            csrf_fields = []

            for inp in inputs:
                name = inp.get("name", "")
                inp_type = inp.get("type", "text").lower()
                value = inp.get("value", "")

                if not name:
                    continue

                if inp_type == "hidden":
                    form_data[name] = value
                    csrf_fields.append(name)
                    continue

                if inp_type == "submit":
                    form_data[name] = value or "submit"
                    continue

                if inp_type == "password" or "password" in name.lower():
                    detected_pass_field = name
                    continue

                if ("user" in name.lower() or "email" in name.lower()
                        or "login" in name.lower() or "mail" in name.lower()
                        or "name" in name.lower()):
                    detected_user_field = name
                    continue

                form_data[name] = value

            # 4. Gunakan field yg dideteksi atau dari parameter
            user_field = username_field or detected_user_field
            pass_field = password_field or detected_pass_field

            if not user_field:
                return "Tidak bisa deteksi field username. Coba kirim dengan parameter username_field='nama_field'"
            if not pass_field:
                return "Tidak bisa deteksi field password. Coba kirim dengan parameter password_field='nama_field'"

            form_data[user_field] = username
            form_data[pass_field] = password

            # 5. Submit form
            r2 = client.post(form_action, data=form_data, timeout=30)

            # 6. Cek apakah login berhasil
            err_indicators = ["invalid", "salah", "error", "wrong password",
                              "username atau password", "login failed",
                              "incorrect", "tidak valid"]
            body_lower = r2.text.lower()
            login_ok = not any(ind in body_lower for ind in err_indicators)

            if login_ok:
                _set_session(domain, client)
                cookie_count = len(client.cookies)
                info = f"Login berhasil ke {domain}!\n"
                info += f"  Cookie tersimpan: {cookie_count} buah\n"
                info += f"  Field username: '{user_field}'\n"
                info += f"  Field password: '{pass_field}'\n"
                if csrf_fields:
                    info += f"  CSRF token fields: {', '.join(csrf_fields)}\n"
                info += "\nSekarang bisa panggil: web_scrape(url=..., use_session=True) untuk ambil data setelah login."
                return info
            else:
                snippet = r2.text[:500].strip()
                return f"Login GAGAL ke {domain}. Response: {snippet[:200]}"

        except ImportError:
            return "Library beautifulsoup4 belum terinstall. Install: pip install beautifulsoup4 lxml"
        except Exception as e:  # noqa: BLE001
            return f"Error login ke {url}: {e}"


def handle_web_logout(args):
    url = args.get("url", "")
    if not url:
        # Logout dari semua session
        count = len(_web_sessions)
        _web_sessions.clear()
        return f"Logout dari semua website ({count} session dihapus)."

    domain = _domain_from_url(url)
    client = _get_session(domain)
    if not client:
        return f"Tidak ada session aktif untuk {domain}."
    try:
        client.close()
    except Exception:  # noqa: BLE001, S110
        pass
    _del_session(domain)
    return f"Logout dari {domain} berhasil. Session dihapus."


def handle_web_session_list(args):
    if not _web_sessions:
        return "Tidak ada session aktif."
    lines = ["Session aktif:"]
    for domain, client in _web_sessions.items():
        cookie_count = len(client.cookies)
        lines.append(f"  - {domain} ({cookie_count} cookie)")
    return "\n".join(lines)


def _filter_by_topic(text, topic):
    lines = text.split("\n")
    topic_lower = topic.lower()
    topic_words = topic_lower.split()
    scored_lines = []
    for i, line in enumerate(lines):
        line_lower = line.lower()
        score = sum(1 for w in topic_words if w in line_lower)
        if score > 0:
            context_start = max(0, i - 2)
            context_end = min(len(lines), i + 3)
            scored_lines.append((score, context_start, context_end))

    if not scored_lines:
        return text

    scored_lines.sort(key=lambda x: x[0], reverse=True)
    included = set()
    for _, start, end in scored_lines[:10]:
        included.update(range(start, end))

    result_lines = [lines[i] for i in sorted(included) if i < len(lines)]
    return "\n".join(result_lines) if result_lines else text


def _search_tavily(query, max_results):
    key = _get_config_key("tavily_api_key")
    if not key:
        return None
    try:
        r = httpx.post(
            "https://api.tavily.com/search",
            json={"query": query, "search_depth": "basic", "max_results": min(max_results, 20), "include_answer": False},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            verify=True, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if not results:
            return None
        lines = []
        for res in results[:max_results]:
            title = unescape(res.get("title", ""))
            url = res.get("url", "")
            content = unescape(res.get("content", ""))
            lines.append(f"- [{title}]({url})")
            if content:
                lines.append(f"  {content}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return None


def _search_tinyfish(query, max_results):
    key = _get_tinyfish_key()
    if not key:
        return None
    try:
        r = httpx.get(
            "https://api.search.tinyfish.ai",
            params={"query": query},
            headers={"X-API-Key": key},
            verify=True,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if not results:
            return None
        lines = []
        for res in results[:max_results]:
            title = unescape(res.get("title", ""))
            url = res.get("url", "")
            snippet = unescape(res.get("snippet", ""))
            lines.append(f"- [{title}]({url})")
            if snippet:
                lines.append(f"  {snippet}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return None


def _search_ddg(query, max_results):
    try:
        r = _fetch_httpx("https://html.duckduckgo.com/html/", params={"q": query})
    except Exception:  # noqa: BLE001
        return None
    if not r.text.strip():
        return None
    results = []
    for block in re.split(r'<div class="result[^"]*"[^>]*>', r.text)[1:]:
        if len(results) >= max_results:
            break
        title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        snippet_m = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', block, re.DOTALL)
        url_m = re.search(r'href="(https?://[^"]+)"', block)
        if title_m and url_m:
            title = unescape(re.sub(r'<[^>]+>', '', title_m.group(1))).strip()
            url = unescape(url_m.group(1))
            snippet = ""
            if snippet_m:
                snippet = unescape(re.sub(r'<[^>]+>', '', snippet_m.group(1))).strip()
            results.append(f"- [{title}]({url})")
            if snippet:
                results.append(f"  {snippet}")
    return "\n".join(results) if results else None


def _search_google(query, max_results):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        r = httpx.get(
            "https://www.google.com/search",
            params={"q": query, "num": min(max_results, 10), "hl": "en"},
            headers=headers, verify=True, timeout=15, follow_redirects=True,
        )
        r.raise_for_status()
    except Exception:  # noqa: BLE001
        return None
    html = r.text
    if "enablejs" in html[:2000].lower():
        return None
    results = []
    for block in re.split(r'<h3[^>]*>', html)[1:]:
        if len(results) >= max_results:
            break
        title_end = block.find("</h3>")
        if title_end == -1:
            continue
        title = unescape(re.sub(r"<[^>]+>", "", block[:title_end]).strip())
        url_m = re.search(r'href="(/url\?q=([^"&]+))"', block)
        if url_m:
            result_url = unescape(url_m.group(2))
        else:
            direct = re.search(r'href="(https?://[^"&]+)"', block)
            result_url = unescape(direct.group(1)) if direct else ""
        if not result_url:
            continue
        snippet = ""
        snip_m = re.search(r'<div[^>]*style="-webkit-line-clamp[^>]*>(.*?)</div>', block, re.DOTALL)
        if not snip_m:
            snip_m = re.search(r'<span[^>]*>(.*?)</span>', block, re.DOTALL)
        if snip_m:
            snippet = unescape(re.sub(r"<[^>]+>", "", snip_m.group(1)).strip())[:200]
        results.append(f"- [{title}]({result_url})")
        if snippet:
            results.append(f"  {snippet}")
    return "\n".join(results) if results else None


def _search_brave(query, max_results, api_key):
    if not api_key:
        return None
    try:
        r = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(max_results, 20)},
            headers={"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": api_key},
            verify=True, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("web", {}).get("results", [])
        if not results:
            return None
        lines = []
        for res in results[:max_results]:
            title = unescape(res.get("title", ""))
            url = res.get("url", "")
            desc = unescape(res.get("description", ""))
            lines.append(f"- [{title}]({url})")
            if desc:
                lines.append(f"  {desc}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return None


def handle_web_search(args):
    query = args.get("query", "")
    source = args.get("source", "auto")
    max_results = min(args.get("max_results", 5), 20)

    if not query:
        return "Error: Query wajib diisi."

    with _Spinner(f"Mencari: {query}"):
        if source == "tinyfish":
            result = _search_tinyfish(query, max_results)
            if result:
                return result
            return f"(tidak ada hasil untuk '{query}' via TinyFish)"

        if source == "tavily":
            result = _search_tavily(query, max_results)
            if result:
                return result
            return f"(tidak ada hasil untuk '{query}' via Tavily)"

        if source == "brave":
            key = _get_brave_key()
            if not key:
                return "Error: Brave API key tidak ditemukan"
            result = _search_brave(query, max_results, key)
            if result:
                return result
            return f"(tidak ada hasil untuk '{query}' via Brave)"

        if source == "google":
            result = _search_google(query, max_results)
            if result:
                return result
            return f"(tidak ada hasil untuk '{query}' via Google)"

        if source == "duckduckgo":
            result = _search_ddg(query, max_results)
            if result:
                return result
            return f"(tidak ada hasil untuk '{query}' via DuckDuckGo)"

        result = _search_tinyfish(query, max_results)
        if result:
            return result
        result = _search_tavily(query, max_results)
        if result:
            return result
        key = _get_brave_key()
        if key:
            result = _search_brave(query, max_results, key)
            if result:
                return result
        result = _search_google(query, max_results)
        if result:
            return result
        result = _search_ddg(query, max_results)
        if result:
            return result
        return f"(tidak ada hasil untuk '{query}')"
