import json, time, httpx
from joki.state import *
from joki.display import stream_print
MAX_TOKENS = 128000

def estimate_tokens(m):
    return len(str(m)) // 4

def _trim_messages(messages, max_tokens):
    total = sum(estimate_tokens(m) for m in messages)
    while total > max_tokens and len(messages) > 2:
        removed = messages.pop(1)
        total -= estimate_tokens(removed)
    return messages

def call_llm(messages):
    global _current_model_config
    
    messages = _trim_messages(messages, MAX_TOKENS)
    
    _joki_cancel.clear()
    _attempted_ids = set()  # track (base_url, model) tuples tried in this call

    for _attempt in range(20):  # safety limit
        mc = _current_model_config
        identity = (mc["base_url"], mc["model"])
        _attempted_ids.add(identity)

        all_keys = mc.get("api_keys") or [mc.get("api_key", "")]
        available = [(i, k) for i, k in enumerate(all_keys) if k not in _exhausted_keys and k]

        if not available:
            fallback = mc.get("fallback", "")
            if fallback and fallback in _MODELS:
                fb_id = (_MODELS[fallback]["base_url"], _MODELS[fallback]["model"])
                if fb_id not in _attempted_ids:
                    _current_model_config = dict(_MODELS[fallback])
                    _console.print(f"[yellow]⚠ Model fallback: {_current_model_config['name']} ({_current_model_config['model']})[/yellow]")
                    continue

            # coba model lain di config.json yang belum dicoba
            found_untried = False
            for km, vm in _MODELS.items():
                vid = (vm["base_url"], vm["model"])
                if vid not in _attempted_ids:
                    _current_model_config = dict(vm)
                    _console.print(f"[yellow]⚠ Model dicoba: {vm['name']} ({vm['model']})[/yellow]")
                    found_untried = True
                    break
            if found_untried:
                continue

            # semua model habis — tampilkan notifikasi
            from rich.panel import Panel
            _console.print(Panel(
                "[bold red]SEMUA MODEL HABIS QUOTA![/bold red]\n\n"
                "Semua API key di semua model yang tersedia sudah habis quota.\n"
                "Gunakan [bold]/reset_quota[/bold] untuk mereset status, atau\n"
                "isi API key baru di [bold]config.json[/bold].",
                title="😵 QUOTA HABIS",
                border_style="red"
            ))
            return {"role": "assistant", "content": "[ERROR] Semua model di config.json sudah habis quota. Gunakan /reset_quota untuk reset."}

        for key_idx, api_key in available:
            if _joki_cancel.is_set():
                return {"role": "assistant", "content": "[CANCELLED] Permintaan dibatalkan oleh pengguna."}

            result = []
            error_data = []

            def _do_request(key=api_key, idx=key_idx, model_cfg=mc):
                try:
                    is_openai = model_cfg["provider"] == "openai"
                    headers = {"Content-Type": "application/json"}
                    if key:
                        headers["Authorization"] = f"Bearer {key}"
                    import random, time
                    MAX_RETRIES = 3
                    RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)
                    for retry in range(MAX_RETRIES):
                        try:
                            if is_openai:
                                url = f"{model_cfg['base_url']}/chat/completions"
                                body = {"model": model_cfg["model"], "messages": messages, "tools": TOOLS, "tool_choice": "auto", "max_tokens": 4096, "stream": True}
                                
                                content_parts = []
                                tool_calls_dict = {}
                                message_role = "assistant"
                                
                                with httpx.stream("POST", url, json=body, headers=headers, timeout=120, follow_redirects=True) as r:
                                    if r.status_code != 200:
                                        err_data = r.read()
                                        raise httpx.HTTPStatusError(f"{err_data}", request=r.request, response=r)
                                    for line in r.iter_lines():
                                        if line.startswith("data: ") and line != "data: [DONE]":
                                            chunk = json.loads(line[6:])
                                            if "choices" in chunk and chunk["choices"]:
                                                delta = chunk["choices"][0].get("delta", {})
                                                if "role" in delta:
                                                    message_role = delta["role"]
                                                if "content" in delta and delta["content"]:
                                                    content = delta["content"]
                                                    print(content, end="", flush=True)
                                                    content_parts.append(content)
                                                if "tool_calls" in delta and delta["tool_calls"]:
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
                                
                                final_msg = {"role": message_role}
                                if content_parts:
                                    final_msg["content"] = "".join(content_parts)
                                    print()
                                else:
                                    final_msg["content"] = None
                                
                                if tool_calls_dict:
                                    final_msg["tool_calls"] = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]
                                
                                result.append(final_msg)
                            else:
                                url = f"{model_cfg['base_url']}/api/chat"
                                body = {"model": model_cfg["model"], "messages": messages, "tools": TOOLS, "stream": False, "max_tokens": 4096}
                                r = httpx.post(url, json=body, headers=headers, timeout=120, follow_redirects=True)
                                data = r.json()
                                if r.status_code != 200:
                                    raise httpx.HTTPStatusError(f"{data}", request=r.request, response=r)
                                err_info = data.get("error") or data.get("error_code")
                                if err_info:
                                    raise httpx.HTTPStatusError(f"{err_info}", request=r.request, response=r)
                                result.append(data["message"])
                            break
                        except RETRYABLE_ERRORS as e:
                            if retry == MAX_RETRIES - 1:
                                raise
                            delay = (2 ** retry) + random.uniform(0, 1)
                            _console.print(f"[yellow]Network error, retry in {delay:.1f}s...[/yellow]")
                            time.sleep(delay)
                except Exception as e:
                    err_resp = getattr(e, "response", None)
                    if err_resp is not None:
                        status = err_resp.status_code
                        body_lower = err_resp.text.lower()
                        is_quota = (
                            status in (429, 402) or
                            any(w in body_lower for w in ["quota", "rate limit", "exhausted",
                                                          "insufficient", "limit reached",
                                                          "too many requests", "billing"])
                        )
                        if is_quota:
                            error_data.append(("quota", f"Key #{idx+1} quota exhausted"))
                        else:
                            error_data.append(("err", f"HTTP {status}: {err_resp.text[:300]}"))
                    else:
                        error_data.append(("err", str(e)))

            req = threading.Thread(target=_do_request, daemon=True)
            req.start()

            with _Spinner("Joki memproses..."):
                while req.is_alive():
                    if _joki_cancel.is_set():
                        req.join(1)
                        break
                    req.join(timeout=0.1)

            if _joki_cancel.is_set():
                return {"role": "assistant", "content": "[CANCELLED] Permintaan dibatalkan oleh pengguna."}

            if error_data:
                etype, emsg = error_data[0]
                if etype == "quota":
                    _exhausted_keys.add(api_key)
                    continue
                return {"role": "assistant", "content": f"[ERROR] LLM call failed: {emsg}"}

            return result[0]

    return {"role": "assistant", "content": "[ERROR] Max attempts reached."}


# ============================================================
# STREAMING & DISPLAY
# ============================================================
