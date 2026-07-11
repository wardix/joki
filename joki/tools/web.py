import httpx
from duckduckgo_search import DDGS
from joki.display import _Spinner


def handle_web_fetch(args):
    url = args.get("url", "")
    if not url:
        return "Error: URL wajib diisi. Contoh: web_fetch(url=\"https://example.com\")"
    with _Spinner("Mengambil konten web"):
        try:
            r = httpx.get(url, timeout=30, follow_redirects=True)
            r.raise_for_status()
            return r.text
        except httpx.TimeoutException:
            return f"Error: Timeout mengambil {url} (30 detik). Coba URL lain atau periksa koneksi."
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} untuk {url}. Mungkin URL tidak valid atau akses ditolak."
        except httpx.InvalidURL:
            return f"Error: URL tidak valid: {url}. Contoh URL yang benar: https://example.com"
        except Exception as e:
            return f"Error: Gagal mengambil {url}: {e}"


def handle_web_search(args):
    query = args.get("query", "")
    if not query:
        return "Error: Query wajib diisi. Contoh: web_search(query=\"cara install python\")"
    with _Spinner("Mencari di web"):
        try:
            results = DDGS().text(
                query, max_results=args.get("max_results", 5))
        except Exception as e:
            return f"Error: Gagal mencari '{query}': {e}. Coba koneksi internet atau query yang berbeda."
    if not results:
        return f"(no results for '{query}')"
    lines = []
    for r in results:
        lines.append(f"- {r['title']}\n  {r['href']}\n  {r['body']}")
    return "\n\n".join(lines)
