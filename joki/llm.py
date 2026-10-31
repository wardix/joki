import copy
import json
import random
import threading
import time

import httpx

from joki.config import _MODELS, _current_model_config
from joki.constants import CORE_TOOLS, MAX_TOKENS, TOOLS, get_tools_for_mode
from joki.display import _border_error, _color_error, _color_warn
from joki.state import *
from joki.thinking import ThinkingDisplay


def _current_tools():
    mode = _current_task_mode
    if mode == MODE_GENERAL:
        # Kurangi overhead: hanya tool inti + tool yang pernah dipakai
        names = set(CORE_TOOLS) | set(_recent_tools)
        return [t for t in TOOLS if t["function"]["name"] in names]
    return get_tools_for_mode(mode)
SUMMARY_TRIGGER_RATIO = 0.65
SUMMARY_WORKING_SET = 8
# Saat SOP refactor aktif, simpan lebih banyak turn terakhir agar file target
# + dependants tidak ter-summarize terlalu dini (refactor multi-file butuh
# konteks lebih panjang dari chat biasa).
REFACTOR_WORKING_SET = 20
_REFACTOR_SOP_MARKER = "[SOP REFACTOR]"
_SUMMARY_MSG_CHARS = 2000
_SUMMARY_TOTAL_CHARS = 120000
_SUMMARY_PROMPT = (
    "Kamu adalah perangkum konteks internal asisten Joki. "
    "Ringkas pesan-pesan percakapan berikut dalam Bahasa Indonesia, padat dan faktual. "
    "Pertahankan: tujuan & permintaan user, keputusan dan rencana yang disepakati, "
    "fakta penting (path file, perintah, angka), hasil tool yang berhasil, "
    "serta masalah/kendala yang masih berjalan. "
    "Hilangkan salam, basa-basi, dan detail yang sudah tidak relevan. "
    "Jangan memanggil tool apa pun. Jangan balas user. "
    "Keluarkan hanya teks ringkasan, maksimal 1500 kata."
)

def estimate_tokens(m):
    return len(str(m)) // 4

def context_usage(messages, tools=None):
    """Hitung estimasi pemakaian konteks sesi ini.

    Return dict: {used, budget, pct, by_role:{role: tokens}, tools,
    messages}. Estimasi kasar (~1 token per 4 karakter, sama seperti
    yang dipakai trim/summarize) — dilabeli ~ di tampilan.
    `budget` = _effective_max_tokens() (context_window dikurangi ruang
    output, dibatasi effective_context bila diset).
    """
    messages = messages or []
    by_role: dict = {}
    for m in messages:
        role = m.get("role", "?") if isinstance(m, dict) else "?"
        by_role[role] = by_role.get(role, 0) + estimate_tokens(m)
    tools_tokens = sum(estimate_tokens(t) for t in (tools or []))
    used = sum(by_role.values()) + tools_tokens
    try:
        budget = int(_effective_max_tokens())
    except Exception:  # noqa: BLE001
        budget = MAX_TOKENS
    pct = (used / budget * 100) if budget > 0 else 0.0
    return {
        "used": used,
        "budget": budget,
        "pct": pct,
        "by_role": by_role,
        "tools": tools_tokens,
        "messages": len(messages),
    }

def _tools_tokens():
    """Estimasi token schema tools aktif. Dipisah agar trigger summarize/trim
    tidak telat (dulu hanya hitung messages, padahal tools 78 skema ikut
    makan konteks). Fail-open 0 bila gagal."""
    try:
        return sum(estimate_tokens(t) for t in _current_tools())
    except Exception:
        return 0


def _messages_budget():
    """Budget khusus messages = effective_max - tools. Minimal 16 agar test
    dengan MAX_TOKENS kecil tetap memicu summarize/trim (dulu clamp 2048
    membuat budget raksasa dan test tiny-budget gagal)."""
    try:
        eff = int(_effective_max_tokens())
    except Exception:
        eff = MAX_TOKENS
    try:
        t = int(_tools_tokens())
    except Exception:
        t = 0
    return max(eff - t, min(16, eff))


def _strip_think_markers(text):
    """Bersihkan sisa marker trace reasoning inline.

    Endpoint NIM mengirim reasoning sebagai content biasa yang diakhiri
    '</think>' (tanpa channel reasoning_content terpisah). Prosa-nya
    dipertahankan (informatif di panel berpikir), hanya marker-nya dibuang
    agar tampilan akhir bersih.
    """
    return text.replace("<think>", "").replace("</think>", "")

def _system_prefix_len(messages):
    """Jumlah pesan system berturut-turut di awal (dilindungi dari trim/summarize)."""
    n = 0
    for m in messages:
        if m.get("role") == "system":
            n += 1
        else:
            break
    return n

def _find_trim_units(messages):
    """Kembalikan daftar (start, end) blok pesan yang boleh dihapus bersama
    tanpa merusak invariant API: assistant.tool_calls harus berpasangan dengan
    tool result miliknya. Pesan system di awal tidak pernah dihapus."""
    units = []
    i = _system_prefix_len(messages)
    n = len(messages)
    while i < n:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tool_ids = {tc.get("id") for tc in m["tool_calls"] if tc.get("id")}
            j = i + 1
            consumed = 0
            while j < n and consumed < len(tool_ids):
                if messages[j].get("role") == "tool" and messages[j].get("tool_call_id") in tool_ids:
                    consumed += 1
                j += 1
            units.append((i, j))
            i = j
        else:
            units.append((i, i + 1))
            i += 1
    return units

def _rebase_read_cache(messages):
    """Bersihkan _read_in_context yang basi setelah trim/summarize.

    Dulu idx tool result disimpan sekali dan tidak pernah diperbarui, sehingga
    setelah pop/insert, dedup baca menunjuk pesan yang salah (pakai isi lama
    yang basi atau gagal dedup). Panggil setiap selesai mutasi messages.
    Fail-open: error diabaikan.
    """
    try:
        if not messages:
            _read_in_context.clear()
            return
        for abs_path in list(_read_in_context.keys()):
            rec = _read_in_context.get(abs_path) or {}
            idx = rec.get("idx")
            head = rec.get("head", "")
            if not isinstance(idx, int) or idx < 0 or idx >= len(messages):
                _read_in_context.pop(abs_path, None)
                continue
            m = messages[idx]
            if m.get("role") != "tool":
                _read_in_context.pop(abs_path, None)
                continue
            if head and not (m.get("content") or "").startswith(head):
                _read_in_context.pop(abs_path, None)
    except Exception:
        pass


def _gemini_system_instruction(messages):
    """Gabung SEMUA pesan system jadi satu systemInstruction (jangan timpa).

    Dulu assignment di dalam loop (`sys_inst = ...` tiap pesan system)
    sehingga system TERAKHIR menang dan system prompt utama hilang diam-diam
    begitu ada suntikan [KONTEKS TUGAS]/[CHECKPOINT TUGAS] (role system).
    Return None bila tak ada system.
    """
    try:
        parts = []
        for m in messages or []:
            if m.get("role") == "system":
                t = (m.get("content") or "").strip()
                if t:
                    parts.append({"text": t})
        if not parts:
            return None
        return {"parts": parts}
    except Exception:  # noqa: BLE001
        return None


def _extractive_summary(old_messages, max_chars=6000):
    """Ringkasan darurat tanpa LLM (untuk provider google / saat quota habis).

    Pertahankan: request user pertama, file yang disebut, keputusan/fakta
    (baris [CHECKPOINT/INDEX]), dan 2 pesan terakhir. Ini jauh lebih baik
    daripada skip total seperti dulu.
    """
    try:
        keep = []
        for m in old_messages:
            c = (m.get("content") or "").strip()
            if not c:
                continue
            if c.startswith(("[CHECKPOINT", "[INDEX", "[RINGKASAN", "[KONTEKS")):
                keep.append(c[:800])
        # Request user pertama selalu dipertahankan.
        for m in old_messages:
            if m.get("role") == "user" and (m.get("content") or "").strip():
                keep.insert(0, "Request awal: " + (m.get("content") or "").strip()[:800])
                break
        tail = []
        for m in old_messages[-2:]:
            c = (m.get("content") or "").strip()
            if c:
                tail.append(f"[{m.get('role')}] {c[:800]}")
        text = "\n".join(keep + tail)
        return text[:max_chars] if text.strip() else None
    except Exception:
        return None


def _trim_messages(messages, max_tokens):
    total = sum(estimate_tokens(m) for m in messages)
    while total > max_tokens and len(messages) > 2:
        units = _find_trim_units(messages)
        # Jangan buang pesan terakhir (turn user yang aktif)
        removable = [u for u in units if u[1] < len(messages)]
        if not removable:
            break
        # Buang yang TERTUA dulu (FIFO), bukan terbesar. Dulu biggest-first
        # sering membuang read_file inti yang baru saja dibaca sehingga
        # agent "lupa" file yang baru dikerjakan. FIFO mempertahankan
        # konteks terbaru yang paling relevan.
        start, end = min(removable, key=lambda u: u[0])
        for k in range(end - 1, start - 1, -1):
            total -= estimate_tokens(messages.pop(k))
    try:
        _rebase_read_cache(messages)
    except Exception:
        pass
    return messages

def _safe_split(messages, keep):
    """Indeks awal suffix terakhir yang 'closed under pairing': semua tool result
    di dalamnya tetap punya pasangan assistant.tool_calls-nya. Prefix system
    selalu dilindungi."""
    proto = _system_prefix_len(messages)
    start = max(proto, len(messages) - keep)
    n = len(messages)
    while True:
        updated = False
        for i in range(start, n):
            m = messages[i]
            if m.get("role") == "tool":
                tcid = m.get("tool_call_id")
                for a in range(i - 1, proto - 1, -1):
                    am = messages[a]
                    if am.get("role") == "assistant" and am.get("tool_calls"):
                        ids = {tc.get("id") for tc in am["tool_calls"]}
                        if tcid in ids:
                            if a < start:
                                start = a
                                updated = True
                            break
        if not updated:
            break
    return start

def _call_summary(model_cfg, old_messages):
    """Panggil LLM (bukan via call_llm agar tidak rekursif) untuk meringkas.
    Mengembalikan teks ringkasan, atau None bila gagal."""
    try:
        parts = []
        for m in old_messages:
            c = (m.get("content") or "").strip()
            if not c:
                continue
            if len(c) > _SUMMARY_MSG_CHARS:
                c = c[: _SUMMARY_MSG_CHARS] + " ...[dipotong]"
            if m.get("role") == "tool":
                parts.append(f"[hasil tool {m.get('tool_call_id', '')}]\n{c}")
            elif m.get("role") == "assistant":
                extra = ""
                if m.get("tool_calls"):
                    names = ", ".join(tc.get("function", {}).get("name", "") for tc in m["tool_calls"])
                    extra = f"\n(alat dipanggil: {names})"
                parts.append(f"[asisten]{extra}\n{c}")
            elif m.get("role") == "user":
                parts.append(f"[user]\n{c}")
            elif m.get("role") == "system":
                parts.append(f"[sistem]\n{c}")
        text = "\n\n---\n\n".join(parts)
        if not text.strip():
            return None
        if len(text) > _SUMMARY_TOTAL_CHARS:
            head = text[: _SUMMARY_TOTAL_CHARS // 2]
            tail = text[-(_SUMMARY_TOTAL_CHARS // 2):]
            text = head + "\n\n...[dipotong tengah]...\n\n" + tail
        summary_messages = [
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": text},
        ]
        provider = model_cfg.get("provider")
        keys = model_cfg.get("api_keys") or [model_cfg.get("api_key", "")]
        for key in keys:
            if _joki_cancel.is_set():
                return None
            content = None
            try:
                if provider == "ollama":
                    url = f"{model_cfg['base_url']}/api/chat"
                    body = {"model": model_cfg["model"], "messages": summary_messages, "stream": False, "options": {"num_predict": 2048}}
                    headers = {"Content-Type": "application/json"}
                    if key:
                        headers["Authorization"] = f"Bearer {key}"
                else:
                    url = f"{model_cfg['base_url']}/chat/completions"
                    body = {"model": model_cfg["model"], "messages": summary_messages, "max_tokens": 2048, "stream": False}
                    try:
                        _sname = str(model_cfg.get("model", "")).lower()
                        _sreason = any(k in _sname for k in ("gpt-5", "gpt-6", "codex", "o1", "o3", "o4"))
                    except Exception:
                        _sreason = False
                    if _sreason:
                        body["max_completion_tokens"] = body.pop("max_tokens", 2048)
                    headers = {"Content-Type": "application/json"}
                    if key:
                        auth_header = model_cfg.get("api_key_header", "Authorization")
                        if auth_header == "Authorization":
                            headers["Authorization"] = f"Bearer {key}"
                        else:
                            headers[auth_header] = key
                r = httpx.post(url, json=body, headers=headers, timeout=120, follow_redirects=True)
                if r.status_code == 200:
                    data = r.json()
                    if provider == "ollama":
                        content = (data.get("message") or {}).get("content", "")
                    else:
                        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
                content = None
            if content and content.strip():
                return content.strip()
        return None
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None

def _effective_max_tokens():
    """Ambil context_window dari model aktif, fallback ke MAX_TOKENS konservatif
    (128000) kalau model belum di-set context_window di config.json.

    Selalu menyisakan ruang untuk token output (max_tokens) dari total
    context_window/fallback, supaya riwayat + jawaban model tidak melebihi
    kapasitas asli model -- baik untuk model yang context_window-nya
    dikonfigurasi eksplisit maupun yang masih pakai fallback."""
    mc = _current_model_config
    cw = mc.get("context_window") or MAX_TOKENS
    # Cap operasional per endpoint: klaim vendor (mis. 1M) sering tidak
    # praktis untuk sesi interaktif (deployment default NIM: 256K).
    # Isi "effective_context" di config untuk memaksa trigger summarize/trim
    # di rentang yang realistis; bila kosong, pakai context_window apa adanya.
    eff = mc.get("effective_context") or cw
    try:
        cw = min(int(cw), int(eff))
    except (TypeError, ValueError):
        cw = int(cw)
    out = mc.get("max_tokens", 8192)
    budget = int(cw) - out
    budget = max(budget, 2048)
    return min(budget, int(cw))

def _summarize_context(messages):
    """Ringkas pesan lama (di luar working set) jadi satu pesan system ringkasan
    bila total estimasi token melewati ambang. Mutasi list in-place. Return list.
    Prefix pesan system (system prompt, project index, checkpoint) dilindungi.

    Ambang dihitung dari _messages_budget() (effective_max dikurangi schema
    tools) agar tidak telat seperti dulu yang hanya hitung messages."""
    mc = copy.deepcopy(_current_model_config)
    proto = _system_prefix_len(messages)
    try:
        budget = int(_messages_budget())
    except Exception:
        budget = int(_effective_max_tokens())
    total = sum(estimate_tokens(m) for m in messages)
    if total <= int(budget * SUMMARY_TRIGGER_RATIO):
        return messages
    # Lindungi konteks refactor: working set lebih besar bila SOP refactor aktif.
    # SOP di-inject sebagai pesan system (cli.py), jadi cek seluruh pesan.
    try:
        _is_refactor_ctx = any(
            _REFACTOR_SOP_MARKER in (m.get("content") or "")
            for m in messages
        )
    except Exception:  # noqa: BLE001
        _is_refactor_ctx = False
    _working_set = REFACTOR_WORKING_SET if _is_refactor_ctx else SUMMARY_WORKING_SET
    if len(messages) - proto <= _working_set + 1:
        return messages
    if _joki_cancel.is_set():
        return messages
    if mc.get("provider") == "google":
        # Dulu skip total -> sesi Gemini hanya trim kasar dan lupa.
        # Sekarang pakai ringkasan ekstraktif (tanpa LLM) agar tetap ada jejak.
        keep_start = _safe_split(messages, _working_set)
        old = messages[proto:keep_start]
        if not old:
            return messages
        summary = _extractive_summary(old)
        if not summary:
            return messages
        messages[:] = (
            messages[:proto]
            + [{"role": "system", "content": f"[RINGKASAN KONTEKS-EKSTRAKTIF]\n{summary}"}]
            + messages[keep_start:]
        )
        _console.print(f"[dim]↻ Konteks diringkas ekstraktif: {len(old)} pesan lama → ringkasan.[/dim]")
        try:
            _rebase_read_cache(messages)
        except Exception:
            pass
        return messages
    keep_start = _safe_split(messages, _working_set)
    old = messages[proto:keep_start]
    if not old:
        return messages
    summary = _call_summary(mc, old)
    if not summary:
        # Fallback ekstraktif hanya saat KRITIS (total > budget) agar tidak
        # agresif: pada 65%-90% kita tunggu LLM berikutnya, tapi saat sudah
        # mau jebol dan LLM gagal (quota/timeout), jangan biarkan trim brutal
        # menghapus tanpa jejak.
        if total <= budget:
            return messages
        summary = _extractive_summary(old)
    if not summary:
        return messages
    messages[:] = (
        messages[:proto]
        + [{"role": "system", "content": f"[RINGKASAN KONTEKS]\n{summary}"}]
        + messages[keep_start:]
    )
    _console.print(f"[dim]↻ Konteks diringkas: {len(old)} pesan lama → ringkasan.[/dim]")
    try:
        _rebase_read_cache(messages)
    except Exception:
        pass
    return messages

def _is_small_talk(messages):
    last = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last = (m.get("content") or "").strip().lower()
            break
    if len(last) > 150:
        return False
    import re
    patterns = [
        r'^(halo|hai|hey|hi|hello|test|tes)\b',
        r'^(iya|ya|ok|oke|okey|okay|okelah|sip|mantap|good|nice|great|okeh)\s*$',
        r'^(thanks?|makasih|terima kasih|trims|thx|thank you)\s*$',
        r'^(siapa|kamu)\b.*',
        r'^(sudah|udah|selesai|done|finish)\s*$',
        r'^(lol|wkwk|haha|hehe|wkwkwk)\s*$',
        r'^(coba|tanya)\s+\w+\s*$',
        r'^(paham|ngerti|ok sip|oke sip|mantul|gas|gaskeun)\s*$',
        r'^iya\s+betul$',
        r'^nggak?\s*ada\s*$',
        r'^gitu\s*$',
    ]
    return any(re.match(p, last) for p in patterns)


def _raise_on_sse_error(chunk, url):
    """Angkat {"error": ...} di dalam stream SSE jadi HTTPStatusError.

    Sebagian provider (mis. NVIDIA NIM) membalas HTTP 200 tetapi isi stream-nya
    hanya satu event error (contoh: "Service temporarily overloaded", code 503).
    Tanpa ini chunk error diabaikan diam-diam -> dianggap sukses dengan konten
    kosong, tanpa retry maupun fallback. Dengan diangkat, klasifikasi
    retry/quota/fallback di call_llm berjalan normal.
    """
    err = chunk.get("error")
    if not err:
        return
    code = 500
    if isinstance(err, dict):
        try:
            code = int(err.get("code") or 0) or 500
        except (TypeError, ValueError):
            code = 500
    req = httpx.Request("POST", url)
    raise httpx.HTTPStatusError(
        str(err)[:300], request=req,
        response=httpx.Response(code, request=req, json=chunk),
    )


def call_llm(messages):
    _summarize_context(messages)
    try:
        messages = _trim_messages(messages, int(_messages_budget()))
    except Exception:
        messages = _trim_messages(messages, _effective_max_tokens())
    
    if not _joki_cancel.is_set():
        _joki_cancel.clear()
    _attempted_ids = set()  # track (base_url, model) tuples tried in this call
    _attempted_keys = set()  # track (api_key, base_url, model) tried in this call (non-quota failures)

    # Snapshot these at call time to avoid races if /model or mode changes mid-stream
    _snapshot_task_mode = _current_task_mode
    _snapshot_tools = _current_tools()

    _show_thinking = not _is_small_talk(messages)

    for _attempt in range(20):  # safety limit
        mc = copy.deepcopy(_current_model_config)
        identity = (mc["base_url"], mc["model"])
        _attempted_ids.add(identity)

        all_keys = mc.get("api_keys") or [mc.get("api_key", "")]
        needs_key = mc.get("provider") != "ollama"
        available = [(i, k) for i, k in enumerate(all_keys) if k not in _exhausted_keys and (k, mc["base_url"], mc["model"]) not in _attempted_keys and (k or not needs_key)]

        if not available:
            fallback = mc.get("fallback", "")
            if fallback and fallback in _MODELS:
                fb_id = (_MODELS[fallback]["base_url"], _MODELS[fallback]["model"])
                if fb_id not in _attempted_ids:
                    _current_model_config.clear()
                    _current_model_config.update(_MODELS[fallback])
                    _console.print(f"[{_color_warn()}]⚠ Model fallback: {_current_model_config['name']} ({_current_model_config['model']})[/{_color_warn()}]")
                    continue

            # coba model lain di config.json yang belum dicoba
            found_untried = False
            for vm in _MODELS.values():
                vid = (vm["base_url"], vm["model"])
                if vid not in _attempted_ids:
                    _current_model_config.clear()
                    _current_model_config.update(vm)
                    _console.print(f"[{_color_warn()}]⚠ Model dicoba: {vm['name']} ({vm['model']})[/{_color_warn()}]")
                    found_untried = True
                    break
            if found_untried:
                continue

            # semua model habis — tampilkan notifikasi
            from rich.panel import Panel
            _console.print(Panel(
                f"[bold {_color_error()}]SEMUA MODEL HABIS QUOTA![/bold {_color_error()}]\n\n"
                "Semua API key di semua model yang tersedia sudah habis quota.\n"
                "Gunakan [bold]/reset_quota[/bold] untuk mereset status, atau\n"
                "isi API key baru di [bold]config.json[/bold].",
                title=f"[bold {_color_error()}]😵 QUOTA HABIS[/bold {_color_error()}]",
                border_style=_border_error()
            ))
            return {"role": "assistant", "content": "[ERROR] Semua model di config.json sudah habis quota. Gunakan /reset_quota untuk reset."}

        for key_idx, api_key in available:
            if _joki_cancel.is_set():
                return {"role": "assistant", "content": "[CANCELLED] Permintaan dibatalkan oleh pengguna."}

            result = []
            error_data = []
            thinking = ThinkingDisplay(disabled=not _show_thinking)

            def _do_request(key=api_key, idx=key_idx, model_cfg=mc, thinking=thinking, result=result, error_data=error_data):
                try:
                    is_openai = model_cfg["provider"] == "openai"
                    headers = {"Content-Type": "application/json"}
                    if key:
                        auth_header = model_cfg.get("api_key_header", "Authorization")
                        if auth_header == "Authorization":
                            headers["Authorization"] = f"Bearer {key}"
                        else:
                            headers[auth_header] = key
                    MAX_RETRIES = 3
                    RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)

                    def _is_retryable_exc(e):
                        if isinstance(e, RETRYABLE_ERRORS):
                            return True
                        # Error server transient (500/502/503/dll) layak di-retry,
                        # tidak seperti 4xx yang harus ditangani key/model berikutnya.
                        if isinstance(e, httpx.HTTPStatusError):
                            return e.response.status_code >= 500
                        return False

                    for retry in range(MAX_RETRIES):
                        if _joki_cancel.is_set():
                            return
                        try:
                            if is_openai:
                                url = f"{model_cfg['base_url']}/chat/completions"
                                resp_max_tokens = model_cfg.get("max_tokens", 8192)
                                body = {"model": model_cfg["model"], "messages": messages, "tools": _snapshot_tools, "tool_choice": "auto", "max_tokens": resp_max_tokens, "stream": True}
                                # Reasoning model OpenAI (gpt-5/codex/o1/o3/o4): pakai
                                # max_completion_tokens, bukan max_tokens (yang ditolak).
                                try:
                                    _mname = str(model_cfg.get("model", "")).lower()
                                    _is_reasoning = any(k in _mname for k in ("gpt-5", "gpt-6", "codex", "o1", "o3", "o4"))
                                except Exception:
                                    _is_reasoning = False
                                if _is_reasoning:
                                    body["max_completion_tokens"] = body.pop("max_tokens", resp_max_tokens)
                                if "reasoning" in model_cfg:
                                    body["reasoning"] = model_cfg["reasoning"]
                                if "extra_body" in model_cfg:
                                    body.update(model_cfg["extra_body"])
                                # Sampling eksplisit per model (mis. rekomendasi NVIDIA
                                # untuk Nemotron: temperature 1.0, top_p 0.95).
                                # Ditaruh setelah extra_body agar field top-level menang.
                                if "temperature" in model_cfg:
                                    body["temperature"] = model_cfg["temperature"]
                                if "top_p" in model_cfg:
                                    body["top_p"] = model_cfg["top_p"]
                                # Reasoning OpenAI tidak dukung temperature/top_p custom:
                                # kirim = error 400. Hapus total untuk reasoning.
                                if _is_reasoning:
                                    body.pop("temperature", None)
                                    body.pop("top_p", None)
                                # Coding butuh deterministik: cap suhu di MODE_CODE
                                # agar tidak halusinasi klaim selesai. Nemotron harian
                                # 0.5 -> 0.3 saat coding (max_tokens tetap 16384).
                                # Reasoning (gpt-5/codex/o) wajib suhu 1.0 -> jangan di-cap.
                                try:
                                    if _snapshot_task_mode == MODE_CODE and "temperature" in body and not _is_reasoning:
                                        body["temperature"] = min(float(body["temperature"]), 0.3)
                                except Exception:
                                    pass
                                if not _show_thinking and isinstance(body.get("chat_template_kwargs"), dict):
                                    # Obrolan ringan: hemat token reasoning via
                                    # medium_effort (didukung Nemotron). Copy agar
                                    # tidak memutasi dict config asli.
                                    _ctk = dict(body["chat_template_kwargs"])
                                    _ctk.setdefault("medium_effort", True)
                                    body["chat_template_kwargs"] = _ctk
                                # Hemat 16k: routine CODE pakai medium_effort, full
                                # thinking hanya untuk refactor (butuh reasoning dalam).
                                # Dipindah setelah small-talk agar small-talk tetap menang.
                                try:
                                    _cb = body.get("chat_template_kwargs")
                                    if (
                                        _show_thinking
                                        and isinstance(_cb, dict)
                                        and _cb.get("enable_thinking")
                                        and _snapshot_task_mode == MODE_CODE
                                        and "medium_effort" not in _cb
                                    ):
                                        _is_ref = any(
                                            _REFACTOR_SOP_MARKER in (mm.get("content") or "")
                                            for mm in messages
                                        )
                                        if not _is_ref:
                                            _ctk2 = dict(_cb)
                                            _ctk2["medium_effort"] = True
                                            body["chat_template_kwargs"] = _ctk2
                                except Exception:
                                    pass

                                content_parts = []
                                tool_calls_dict = {}
                                message_role = "assistant"
                                _truncated = False
                                
                                with httpx.stream("POST", url, json=body, headers=headers, timeout=120, follow_redirects=True) as r:
                                    if r.status_code != 200:
                                        err_data = r.read()
                                        raise httpx.HTTPStatusError(f"{err_data}", request=r.request, response=r)
                                    for line in r.iter_lines():
                                        if _joki_cancel.is_set():
                                            return
                                        if line.startswith("data: ") and line != "data: [DONE]":
                                            chunk = json.loads(line[6:])
                                            _raise_on_sse_error(chunk, url)
                                            if chunk.get("choices"):
                                                delta = chunk["choices"][0].get("delta", {})
                                                if "role" in delta:
                                                    message_role = delta["role"]
                                                reasoning_content = delta.get("reasoning_content")
                                                if reasoning_content:
                                                    thinking.push(reasoning_content)

                                                if delta.get("content"):
                                                    content = delta["content"]
                                                    content_parts.append(content)
                                                    thinking.push(content)
                                                if delta.get("tool_calls"):
                                                    for tc in delta["tool_calls"]:
                                                        tc_idx = tc["index"]
                                                        if tc_idx not in tool_calls_dict:
                                                            tool_calls_dict[tc_idx] = tc
                                                        else:
                                                            if "function" in tc:
                                                                if "name" in tc["function"]:
                                                                    tool_calls_dict[tc_idx]["function"].setdefault("name", "")
                                                                    tool_calls_dict[tc_idx]["function"]["name"] += tc["function"]["name"]
                                                                if "arguments" in tc["function"]:
                                                                    tool_calls_dict[tc_idx]["function"].setdefault("arguments", "")
                                                                    tool_calls_dict[tc_idx]["function"]["arguments"] += tc["function"]["arguments"]
                                                finish_reason = chunk["choices"][0].get("finish_reason")
                                                if finish_reason == "length":
                                                    _truncated = True
                                
                                final_msg = {"role": message_role}
                                if content_parts:
                                    full_content = "".join(content_parts)
                                    full_content = _strip_think_markers(full_content)
                                    if _truncated:
                                        full_content += "\n\n_[WARNING: Respon terpotong karena mencapai batas max_tokens. Gunakan perintah lebih spesifik atau tingkatkan max_tokens di config.json.]_"
                                    final_msg["content"] = full_content
                                else:
                                    final_msg["content"] = ""

                                if tool_calls_dict:
                                    _sorted_tc = [tool_calls_dict[i] for i in sorted(tool_calls_dict)]
                                    # Hemat 16k Nemotron: batasi 6 tool_calls per turn.
                                    if len(_sorted_tc) > 6:
                                        _sorted_tc = _sorted_tc[:6]
                                        _tc_note = (
                                            "\n\n_[WARNING: 6+ tool_calls dipangkas agar muat 16384 "
                                            "max_tokens Nemotron. Pecah task jadi kecil.]_"
                                        )
                                        if content_parts:
                                            full_content = "".join(content_parts)
                                            full_content = _strip_think_markers(full_content) + _tc_note
                                            final_msg["content"] = full_content
                                        else:
                                            final_msg["content"] = _tc_note.strip()
                                    final_msg["tool_calls"] = _sorted_tc

                                result.append(final_msg)
                            elif model_cfg["provider"] == "google":
                                url = f"{model_cfg['base_url']}/models/{model_cfg['model']}:streamGenerateContent?alt=sse"
                                headers_g = {"Content-Type": "application/json"}
                                if key:
                                    headers_g["x-goog-api-key"] = key
                                sys_inst = None
                                contents = []
                                tc_id_to_name = {}
                                for m2 in messages:
                                    if m2.get("role") == "assistant" and "tool_calls" in m2:
                                        for tc2 in m2["tool_calls"]:
                                            tc_id_to_name[tc2.get("id", "")] = tc2["function"]["name"]
                                for m2 in messages:
                                    r2 = m2.get("role", "")
                                    if r2 == "system":
                                        # Ditangani setelah loop via _gemini_system_instruction
                                        # (gabung semua, jangan timpa).
                                        continue
                                    elif r2 == "user":
                                        txt = m2.get("content", "")
                                        if txt:
                                            contents.append({"role": "user", "parts": [{"text": txt}]})
                                    elif r2 == "assistant":
                                        parts = []
                                        txt = m2.get("content", "")
                                        if txt:
                                            parts.append({"text": txt})
                                        for tc2 in m2.get("tool_calls", []):
                                            try:
                                                a2 = json.loads(tc2["function"]["arguments"]) if isinstance(tc2["function"]["arguments"], str) else tc2["function"]["arguments"]
                                            except Exception:  # noqa: BLE001
                                                a2 = {}
                                            parts.append({"functionCall": {"name": tc2["function"]["name"], "args": a2}})
                                        if parts:
                                            contents.append({"role": "model", "parts": parts})
                                    elif r2 == "tool":
                                        fn = tc_id_to_name.get(m2.get("tool_call_id", ""), "")
                                        ct = m2.get("content", "")
                                        if fn:
                                            contents.append({"role": "user", "parts": [{"functionResponse": {"name": fn, "response": {"response": ct}}}]})
                                        elif ct:
                                            contents.append({"role": "user", "parts": [{"text": ct}]})
                                # Gabung semua system (prompt utama + checkpoint +
                                # konteks-tugas) — jangan biarkan yang terakhir
                                # menimpa yang lain.
                                sys_inst = _gemini_system_instruction(messages)
                                g_tools = []
                                for t2 in _snapshot_tools:
                                    f2 = t2.get("function", {})
                                    g_tools.append({"functionDeclarations": [{"name": f2.get("name", ""), "description": f2.get("description", ""), "parameters": f2.get("parameters", {})}]})
                                body = {
                                    "contents": contents,
                                    "tools": g_tools,
                                    "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
                                    "generationConfig": {"maxOutputTokens": model_cfg.get("max_tokens", 8192)},
                                    "thinkingLevel": "LOW"
                                }
                                if "temperature" in model_cfg:
                                    body["generationConfig"]["temperature"] = model_cfg["temperature"]
                                if "top_p" in model_cfg:
                                    body["generationConfig"]["topP"] = model_cfg["top_p"]
                                if sys_inst:
                                    body["systemInstruction"] = sys_inst
                                content_parts = []
                                tool_calls_dict = {}
                                message_role = "model"
                                _truncated = False
                                with httpx.stream("POST", url, json=body, headers=headers_g, timeout=120, follow_redirects=True) as r:
                                    if r.status_code != 200:
                                        err_data = r.read()
                                        raise httpx.HTTPStatusError(f"{err_data}", request=r.request, response=r)
                                    for line in r.iter_lines():
                                        if _joki_cancel.is_set():
                                            return
                                        if line.startswith("data: ") and line != "data: [DONE]":
                                            chunk = json.loads(line[6:])
                                            _raise_on_sse_error(chunk, url)
                                            if chunk.get("candidates"):
                                                cand = chunk["candidates"][0]
                                                if "content" in cand and "parts" in cand["content"]:
                                                    for part in cand["content"]["parts"]:
                                                        if "text" in part:
                                                            content_parts.append(part["text"])
                                                            thinking.push(part["text"])
                                                        elif "functionCall" in part:
                                                            fc = part["functionCall"]
                                                            tc_idx = len(tool_calls_dict)
                                                            tool_calls_dict[tc_idx] = {
                                                                "id": f"call_{tc_idx}",
                                                                "type": "function",
                                                                "function": {
                                                                    "name": fc.get("name", ""),
                                                                    "arguments": json.dumps(fc.get("args", {}))
                                                                }
                                                            }
                                                if "finishReason" in cand:
                                                    fr = cand["finishReason"]
                                                    if fr == "MAX_TOKENS":
                                                        _truncated = True
                                final_msg: dict = {"role": message_role}
                                if content_parts:
                                    full_content = "".join(content_parts)
                                    if _truncated:
                                        full_content += "\n\n_[WARNING: Respon terpotong karena mencapai batas max_tokens. Gunakan perintah lebih spesifik atau tingkatkan max_tokens di config.json.]_"
                                    final_msg["content"] = full_content
                                else:
                                    final_msg["content"] = ""
                                if tool_calls_dict:
                                    final_msg["tool_calls"] = [tool_calls_dict[i] for i in sorted(tool_calls_dict)]
                                result.append(final_msg)
                            else:
                                url = f"{model_cfg['base_url']}/api/chat"
                                body = {"model": model_cfg["model"], "messages": messages, "tools": _current_tools(), "stream": False, "max_tokens": model_cfg.get("max_tokens", 8192)}
                                r = httpx.post(url, json=body, headers=headers, timeout=120, follow_redirects=True)
                                data = r.json()
                                if r.status_code != 200:
                                    raise httpx.HTTPStatusError(f"{data}", request=r.request, response=r)
                                err_info = data.get("error") or data.get("error_code")
                                if err_info:
                                    raise httpx.HTTPStatusError(f"{err_info}", request=r.request, response=r)
                                result.append(data["message"])
                            break
                        except Exception as _re:
                            if not _is_retryable_exc(_re):
                                raise
                            if retry == MAX_RETRIES - 1:
                                raise
                            delay = (2 ** retry) + random.uniform(0, 1)
                            _console.print(f"[{_color_warn()}]Network/server error, retry in {delay:.1f}s...[/{_color_warn()}]")
                            time.sleep(delay)
                except Exception as e:  # noqa: BLE001
                    err_resp = getattr(e, "response", None)
                    if err_resp is not None:
                        status = err_resp.status_code
                        body_lower = err_resp.text.lower()
                        is_quota = (
                            status in (429, 402) or
                            any(w in body_lower for w in ["quota", "rate limit", "exhausted",
                                                          "insufficient", "limit reached",
                                                          "too many requests", "billing",
                                                          "usage limit"])
                        )
                        if is_quota:
                            error_data.append(("quota", f"Key #{idx+1} quota exhausted"))
                        else:
                            error_data.append(("err", f"HTTP {status}: {err_resp.text[:300]}"))
                    else:
                        error_data.append(("err", str(e)))

            req = threading.Thread(target=_do_request, daemon=False)
            req.start()

            try:
                with thinking:
                    while req.is_alive():
                        if _joki_cancel.is_set():
                            req.join(timeout=2.0)
                            break
                        thinking.update()
                        req.join(timeout=0.02)
            finally:
                if req.is_alive():
                    req.join(timeout=2.0)

            if _joki_cancel.is_set():
                return {"role": "assistant", "content": "[CANCELLED] Permintaan dibatalkan oleh pengguna."}

            if error_data:
                if error_data[0][0] == "quota":
                    _exhausted_keys.add(api_key)
                else:
                    _attempted_keys.add((api_key, mc["base_url"], mc["model"]))
                continue

            return result[0]

    return {"role": "assistant", "content": "[ERROR] Max attempts reached."}


# ============================================================
# STREAMING & DISPLAY
# ============================================================
