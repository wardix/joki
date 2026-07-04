import httpx
from duckduckgo_search import DDGS
from joki.display import _Spinner


def handle_web_fetch(args):
    with _Spinner("Mengambil konten web"):
        r = httpx.get(args["url"], timeout=30, follow_redirects=True)
        r.raise_for_status()
    return r.text


def handle_web_search(args):
    with _Spinner("Mencari di web"):
        results = DDGS().text(
            args["query"], max_results=args.get(
                "max_results", 5))
    if not results:
        return "(no results)"
    lines = []
    for r in results:
        lines.append(f"- {r['title']}\n  {r['href']}\n  {r['body']}")
    return "\n\n".join(lines)
