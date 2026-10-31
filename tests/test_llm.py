import json
from unittest.mock import MagicMock, patch

import pytest

from joki.llm import (
    MODE_GENERAL,
    _call_summary,
    _current_tools,
    _is_small_talk,
    _safe_split,
    _summarize_context,
    _system_prefix_len,
    _trim_messages,
    call_llm,
)


def _msg(content):
    return [{"role": "user", "content": content}]


def test_short_task_prompts_not_small_talk():
    assert not _is_small_talk(_msg("jalankan test sekarang"))
    assert not _is_small_talk(_msg("cek file config"))
    assert not _is_small_talk(_msg("fix bug ini"))


def test_greetings_still_small_talk():
    assert _is_small_talk(_msg("halo"))
    assert _is_small_talk(_msg("makasih"))
    assert _is_small_talk(_msg("oke sip"))


def _make_sse_lines(chunks):
    lines = []
    for c in chunks:
        lines.append(f"data: {json.dumps(c)}")
    lines.append("data: [DONE]")
    return lines


@patch("joki.llm._MODELS", {})
@patch("joki.llm._current_model_config", {
    "name": "NVIDIA Nemotron",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "model": "nvidia/nemotron-3-ultra-550b-a55b",
    "api_keys": ["nvapi-test"],
    "provider": "openai",
    "max_tokens": 1024,
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
@patch("joki.llm.time.sleep")
def test_sse_error_chunk_not_swallowed(mock_sleep, mock_td, mock_stream):
    """Regresi: NVIDIA NIM balas HTTP 200 tapi isi stream {"error": 503}.

    Dulu chunk tanpa "choices" diabaikan -> dianggap sukses dengan konten
    kosong. Sekarang harus diangkat jadi error (retry/fallback), bukan diam.
    """
    sse_chunks = [
        {"error": {"message": "Service temporarily overloaded",
                   "type": "service_unavailable", "code": 503}},
    ]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    result = call_llm([{"role": "user", "content": "Jalankan pengecekan koneksi ke server."}])
    assert result["content"], "respons kosong — error SSE ditelan diam-diam lagi"
    assert result["content"].startswith("[ERROR]")
    assert mock_sleep.called, "tidak ada retry sama sekali"


@patch("joki.llm._current_model_config", {
    "name": "Test Model",
    "base_url": "http://localhost:11434",
    "model": "test-model",
    "api_keys": ["test-key"],
    "provider": "ollama"
})
@patch("joki.llm.httpx.post")
def test_call_llm_success(mock_post):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "Hello world"}
    }
    mock_post.return_value = mock_response

    messages = [{"role": "user", "content": "Hi"}]
    result = call_llm(messages)

    assert result["role"] == "assistant"
    assert result["content"] == "Hello world"
    mock_post.assert_called_once()


@patch("joki.llm._current_model_config", {
    "name": "DeepSeek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "api_keys": ["sk-test"],
    "provider": "openai",
    "max_tokens": 8192,
    "reasoning": {"max_tokens": 2048},
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_reasoning_content_not_in_final_response(mock_td, mock_stream):
    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
        {"choices": [{"delta": {"reasoning_content": "Hmm, let me think about this..."}, "index": 0}]},
        {"choices": [{"delta": {"reasoning_content": " I need to analyze step by step."}, "index": 0}]},
        {"choices": [{"delta": {"content": "Here is the answer:"}, "index": 0}]},
        {"choices": [{"delta": {"content": " 42"}, "index": 0, "finish_reason": "stop"}]},
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    messages = [{"role": "user", "content": "What is the meaning of life?"}]
    result = call_llm(messages)

    assert result["role"] == "assistant"
    assert result["content"] == "Here is the answer: 42"
    assert "Hmm, let me think" not in result["content"]


@patch("joki.llm._current_model_config", {
    "name": "DeepSeek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "api_keys": ["sk-test"],
    "provider": "openai",
    "max_tokens": 8192,
    "reasoning": {"max_tokens": 2048},
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_reasoning_param_in_request_body(mock_td, mock_stream):
    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
        {"choices": [{"delta": {"content": "OK"}, "index": 0, "finish_reason": "stop"}]},
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    messages = [{"role": "user", "content": "Hi"}]
    call_llm(messages)

    _, kwargs = mock_stream.call_args
    sent_body = kwargs["json"]
    assert "reasoning" in sent_body
    assert sent_body["reasoning"] == {"max_tokens": 2048}


@patch("joki.llm._current_model_config", {
    "name": "DeepSeek V4 Flash (NVIDIA)",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "model": "deepseek-ai/deepseek-v4-flash-0731",
    "api_keys": ["nvapi-test"],
    "provider": "openai",
    "max_tokens": 16384,
    "extra_body": {"chat_template_kwargs": {"thinking": True, "reasoning_effort": "low"}},
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_extra_body_merged_into_request_body(mock_td, mock_stream):
    """Field extra_body di config model harus di-unwrap dan digabung sebagai
    key top-level di body request (bukan jadi key literal 'extra_body'),
    mengikuti konvensi OpenAI SDK/vLLM (mis. chat_template_kwargs untuk
    NVIDIA NIM)."""
    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
        {"choices": [{"delta": {"content": "OK"}, "index": 0, "finish_reason": "stop"}]},
    ]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    call_llm([{"role": "user", "content": "Refactor modul auth agar lebih rapi"}])

    _, kwargs = mock_stream.call_args
    sent_body = kwargs["json"]
    assert "chat_template_kwargs" in sent_body
    assert sent_body["chat_template_kwargs"] == {"thinking": True, "reasoning_effort": "low"}


@patch("joki.llm._current_model_config", {
    "name": "Nemotron NIM",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "model": "nvidia/nemotron-3-ultra-550b-a55b",
    "api_keys": ["nvapi-test"],
    "provider": "openai",
    "max_tokens": 16384,
    "temperature": 1.0,
    "top_p": 0.95,
    "extra_body": {"chat_template_kwargs": {"enable_thinking": True}},
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_small_talk_adds_medium_effort_and_sampling(mock_td, mock_stream):
    """Obrolan ringan: medium_effort otomatis ditambahkan ke
    chat_template_kwargs, dan temperature/top_p config diteruskan."""
    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
        {"choices": [{"delta": {"content": "OK"}, "index": 0, "finish_reason": "stop"}]},
    ]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    call_llm([{"role": "user", "content": "Hi"}])

    _, kwargs = mock_stream.call_args
    sent_body = kwargs["json"]
    assert sent_body["chat_template_kwargs"] == {"enable_thinking": True, "medium_effort": True}
    assert sent_body["temperature"] == 1.0
    assert sent_body["top_p"] == 0.95


@patch("joki.llm._current_model_config", {
    "name": "Model Context Besar",
    "base_url": "https://api.test.com",
    "model": "big-context-model",
    "api_keys": ["sk-test"],
    "provider": "openai",
    "max_tokens": 32768,
    "context_window": 262144,
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_context_window_overrides_default_max_tokens_for_trim(mock_td, mock_stream):
    """Model dengan context_window yang dikonfigurasi lebih besar dari
    MAX_TOKENS default (128000) tidak boleh kehilangan riwayat percakapan
    yang sebenarnya masih muat di context window aslinya. Regresi untuk bug:
    _trim_messages dulu selalu pakai MAX_TOKENS global (128000) untuk SEMUA
    model, tanpa peduli context window asli model tersebut."""
    long_history = [{"role": "system", "content": "system prompt"}]
    long_history += [{"role": "user", "content": "x" * 4000} for _ in range(190)]
    long_history += [{"role": "user", "content": "pertanyaan terakhir"}]
    original_length = len(long_history)

    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant", "content": "jawaban"}, "index": 0}]},
        {"choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]},
    ]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    call_llm(long_history)

    _, kwargs = mock_stream.call_args
    sent_messages = kwargs["json"]["messages"]
    # Total estimasi token riwayat ini > 128000 (MAX_TOKENS default) tapi
    # < 262144 (context_window model ini) -- jadi TIDAK boleh terpangkas.
    assert len(sent_messages) == original_length, (
        f"riwayat terpangkas padahal masih muat di context_window model: "
        f"{len(sent_messages)} != {original_length}"
    )


@patch("joki.llm._current_model_config", {
    "name": "Model Tanpa context_window",
    "base_url": "https://api.test.com",
    "model": "no-context-window-model",
    "api_keys": ["sk-test"],
    "provider": "openai",
    "max_tokens": 8192,
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_missing_context_window_falls_back_to_max_tokens_default(mock_td, mock_stream):
    """Model yang belum di-set context_window di config.json harus tetap
    fallback ke MAX_TOKENS default (128000) -- backward compat, jangan
    sampai model lama yang belum diupdate config-nya malah error/overflow."""
    long_history = [{"role": "system", "content": "system prompt"}]
    long_history += [{"role": "user", "content": "x" * 4000} for _ in range(190)]
    long_history += [{"role": "user", "content": "pertanyaan terakhir"}]
    original_length = len(long_history)

    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant", "content": "jawaban"}, "index": 0}]},
        {"choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]},
    ]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    call_llm(long_history)

    _, kwargs = mock_stream.call_args
    sent_messages = kwargs["json"]["messages"]
    # Total estimasi token riwayat ini > 128000 -- HARUS terpangkas karena
    # model ini tidak punya context_window custom, fallback ke MAX_TOKENS.
    assert len(sent_messages) < original_length, (
        "riwayat seharusnya terpangkas karena model tidak punya context_window "
        "custom dan totalnya melebihi MAX_TOKENS default"
    )


@patch("joki.llm._current_model_config", {
    "name": "NoReasoning",
    "base_url": "https://api.example.com",
    "model": "no-reasoning",
    "api_keys": ["sk-test"],
    "provider": "openai",
    "max_tokens": 4096,
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_no_reasoning_param_when_not_configured(mock_td, mock_stream):
    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
        {"choices": [{"delta": {"content": "OK"}, "index": 0, "finish_reason": "stop"}]},
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    messages = [{"role": "user", "content": "Hi"}]
    call_llm(messages)

    _, kwargs = mock_stream.call_args
    sent_body = kwargs["json"]
    assert "reasoning" not in sent_body


@patch("joki.llm._current_model_config", {
    "name": "Test",
    "base_url": "https://api.test.com",
    "model": "test",
    "api_keys": ["sk-test"],
    "provider": "openai",
    "max_tokens": 4096,
})
@patch("joki.llm.httpx.stream")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_tool_call_only_delta_does_not_push_gear(mock_td, mock_stream):
    """Tool call tanpa content sama sekali tidak boleh nge-push placeholder
    gear (⚙️) ke ThinkingDisplay -- panel 'Joki Berpikir (selesai)' seharusnya
    kosong/tidak muncul kalau tidak ada konten reasoning yang beneran."""
    sse_chunks = [
        {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
        ]}, "index": 0}]},
        {"choices": [{"delta": {}, "index": 0, "finish_reason": "tool_calls"}]},
    ]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = _make_sse_lines(sse_chunks)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_response
    mock_stream.return_value = mock_cm

    result = call_llm([{"role": "user", "content": "baca file"}])

    assert "tool_calls" in result
    thinking_instance = mock_td.return_value.__enter__.return_value
    push_calls = [c.args[0] for c in thinking_instance.push.call_args_list]
    assert not any("\u2699" in str(a) for a in push_calls), (
        f"gear placeholder masih di-push: {push_calls}"
    )


@patch("joki.llm._current_model_config", {
    "name": "Ollama Test",
    "base_url": "http://localhost:11434",
    "model": "test",
    "api_keys": [],
    "provider": "ollama",
    "max_tokens": 4096,
})
@patch("joki.llm.httpx.post")
@patch("joki.llm.ThinkingDisplay", autospec=True)
def test_ollama_nonstreaming_tool_call_does_not_push_gear(mock_td, mock_post):
    """Sama seperti di atas, tapi untuk jalur non-streaming (Ollama)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": {}}}
        ]}
    }
    mock_post.return_value = mock_response

    result = call_llm([{"role": "user", "content": "baca file"}])

    assert result["role"] == "assistant"
    thinking_instance = mock_td.return_value.__enter__.return_value
    push_calls = [c.args[0] for c in thinking_instance.push.call_args_list]
    assert not any("\u2699" in str(a) for a in push_calls), (
        f"gear placeholder masih di-push: {push_calls}"
    )


def _assert_pairing_ok(messages):
    tool_ids = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                assert tc.get("id") in tool_ids, "assistant.tool_calls tanpa pasangan tool result"
    declared = set()
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                declared.add(tc.get("id"))
    for m in messages:
        if m.get("role") == "tool":
            assert m.get("tool_call_id") in declared, "tool result yatim (tanpa assistant)"


def test_trim_messages_preserves_pairs():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "kerjakan X"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "X" * 4000},
        {"role": "assistant", "content": "Langkah berikutnya"},
        {"role": "user", "content": "lanjut"},
    ]
    _trim_messages(messages, max_tokens=500)
    assert messages[0]["role"] == "system"
    _assert_pairing_ok(messages)
    assert all(m.get("role") != "tool" for m in messages)


def test_trim_messages_keeps_last_message():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "A" * 2000},
        {"role": "assistant", "content": "B" * 2000},
        {"role": "user", "content": "C" * 2000},
    ]
    _trim_messages(messages, max_tokens=100)
    assert len(messages) >= 2
    assert messages[-1]["content"] == "C" * 2000


def test_safe_split_extends_to_paired_assistant():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "user", "content": "ok"},
    ]
    assert _safe_split(messages, keep=2) == 1


def test_safe_split_no_extension_needed():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "user", "content": "ok"},
    ]
    assert _safe_split(messages, keep=3) == 2


@patch("joki.llm._call_summary", return_value="ringkasan tes")
@patch("joki.llm._current_model_config", {
    "provider": "openai",
    "base_url": "https://api.example.com",
    "model": "m",
    "api_keys": ["k"],
})
def test_summarize_context_condenses_over_threshold(mock_summary):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "A" * 300},
        {"role": "assistant", "content": "B" * 300},
        {"role": "user", "content": "C" * 300},
        {"role": "user", "content": "D" * 300},
    ]
    with patch("joki.llm.MAX_TOKENS", 100), patch("joki.llm.SUMMARY_WORKING_SET", 2):
        _summarize_context(messages)
    mock_summary.assert_called_once()
    assert messages[1]["role"] == "system"
    assert "[RINGKASAN KONTEKS]" in messages[1]["content"]
    assert messages[-2:] == [
        {"role": "user", "content": "C" * 300},
        {"role": "user", "content": "D" * 300},
    ]


@patch("joki.llm._call_summary", return_value=None)
@patch("joki.llm._current_model_config", {
    "provider": "openai",
    "base_url": "https://api.example.com",
    "model": "m",
    "api_keys": ["k"],
})
def test_summarize_context_falls_back_when_summary_fails(mock_summary):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "A" * 300},
        {"role": "assistant", "content": "B" * 300},
        {"role": "user", "content": "C" * 300},
        {"role": "user", "content": "D" * 300},
    ]
    with patch("joki.llm.MAX_TOKENS", 100), patch("joki.llm.SUMMARY_WORKING_SET", 2):
        _summarize_context(messages)
    assert mock_summary.called
    # Perilaku baru: saat KRITIS (total > budget) dan LLM gagal, pakai
    # ringkasan ekstraktif agar tidak hilang tanpa jejak (anti-lupa).
    assert messages[1]["role"] == "system"
    assert "[RINGKASAN KONTEKS]" in messages[1]["content"]


@patch("joki.llm.httpx.post")
def test_call_summary_openai_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": "ringkasan"}}]}
    mock_post.return_value = mock_resp
    cfg = {"provider": "openai", "base_url": "https://x/v1", "model": "m", "api_keys": ["k"]}
    out = _call_summary(cfg, [{"role": "user", "content": "isi panjang"}])
    assert out == "ringkasan"
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["stream"] is False


@patch("joki.llm.httpx.post")
def test_call_summary_rotates_keys_on_quota(mock_post):
    r1 = MagicMock()
    r1.status_code = 429
    r2 = MagicMock()
    r2.status_code = 200
    r2.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    mock_post.side_effect = [r1, r2]
    cfg = {"provider": "openai", "base_url": "https://x/v1", "model": "m", "api_keys": ["a", "b"]}
    out = _call_summary(cfg, [{"role": "user", "content": "x"}])
    assert out == "ok"
    assert mock_post.call_count == 2


@patch("joki.llm.httpx.post")
def test_call_summary_all_keys_fail_returns_none(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_post.return_value = mock_resp
    cfg = {"provider": "openai", "base_url": "https://x/v1", "model": "m", "api_keys": ["a", "b"]}
    assert _call_summary(cfg, [{"role": "user", "content": "x"}]) is None
    assert mock_post.call_count == 2


def test_system_prefix_protected_from_trim():
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "system", "content": "[INDEX PROYEK] map"},
        {"role": "system", "content": "[CHECKPOINT TUGAS] cek"},
        {"role": "user", "content": "A" * 3000},
        {"role": "assistant", "content": "B" * 3000},
        {"role": "user", "content": "C" * 3000},
    ]
    _trim_messages(messages, max_tokens=100)
    assert len(messages) >= 4
    assert messages[0]["content"] == "prompt"
    assert messages[1]["content"].startswith("[INDEX PROYEK]")
    assert messages[2]["content"].startswith("[CHECKPOINT TUGAS]")


def test_system_prefix_len_counts_leading_system():
    messages = [
        {"role": "system", "content": "a"},
        {"role": "system", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "system", "content": "d"},
    ]
    assert _system_prefix_len(messages) == 2


@patch("joki.llm._call_summary", return_value="ringkasan")
@patch("joki.llm._current_model_config", {
    "provider": "openai",
    "base_url": "https://api.example.com",
    "model": "m",
    "api_keys": ["k"],
})
def test_summarize_preserves_system_prefix(mock_summary):
    messages = [
        {"role": "system", "content": "prompt"},
        {"role": "system", "content": "[INDEX PROYEK] map"},
        {"role": "system", "content": "[CHECKPOINT TUGAS] cek"},
        {"role": "user", "content": "A" * 300},
        {"role": "assistant", "content": "B" * 300},
        {"role": "user", "content": "C" * 300},
        {"role": "user", "content": "D" * 300},
    ]
    with patch("joki.llm.MAX_TOKENS", 100), patch("joki.llm.SUMMARY_WORKING_SET", 2):
        _summarize_context(messages)
    assert messages[0]["content"] == "prompt"
    assert messages[1]["content"].startswith("[INDEX PROYEK]")
    assert messages[2]["content"].startswith("[CHECKPOINT TUGAS]")
    assert "[RINGKASAN KONTEKS]" in messages[3]["content"]
    assert messages[-2:] == [
        {"role": "user", "content": "C" * 300},
        {"role": "user", "content": "D" * 300},
    ]


@patch("joki.llm._current_task_mode", MODE_GENERAL)
@patch("joki.llm._recent_tools", set())
def test_current_tools_general_only_core():
    tools = _current_tools()
    names = {t["function"]["name"] for t in tools}
    assert "read_file" in names
    assert "run_command" in names
    assert "port_scan" not in names  # bukan core, belum pernah dipakai


@patch("joki.llm._current_task_mode", MODE_GENERAL)
@patch("joki.llm._recent_tools", {"port_scan"})
def test_current_tools_general_adds_recent():
    tools = _current_tools()
    names = {t["function"]["name"] for t in tools}
    assert "port_scan" in names


# --- Test tambahan: context_window & trimming (level unit, langsung ke
# _trim_messages/_summarize_context) -- komplementer dengan test di atas yang
# menguji lewat call_llm() end-to-end. Dipertahankan keduanya: yang di atas
# menguji WIRING (apakah call_llm benar2 pakai mekanisme yang benar), yang di
# bawah ini menguji perilaku _trim_messages/_summarize_context itu sendiri
# secara terisolasi. ---

def _umsg(text: str, role: str = "user") -> dict:
    return {"role": role, "content": text}


@pytest.fixture
def _model_ctx_guard():
    """Simpan & kembalikan _current_model_config asli setelah test selesai,
    supaya mutasi langsung (bukan lewat @patch) di test-test ini tidak bocor
    ke test lain di suite yang sama."""
    import joki.llm as l
    original = dict(l._current_model_config)
    yield l
    l._current_model_config.clear()
    l._current_model_config.update(original)


def _set_model_ctx(llm_mod, context_window: int = 128000, max_tokens: int = 8192):
    """Paksa _current_model_config ke nilai yang terkontrol. Selalu dipakai
    bersama fixture _model_ctx_guard supaya state ter-restore setelah test."""
    llm_mod._current_model_config.clear()
    llm_mod._current_model_config.update({
        "context_window": context_window,
        "max_tokens": max_tokens,
        "provider": "openai",
    })


def test_context_window_is_used_for_summarize_threshold(_model_ctx_guard):
    """Jika total token < 65% context_window, tidak perlu summarize."""
    l = _model_ctx_guard
    _set_model_ctx(l, context_window=1_000_000)
    msgs = [_umsg("x" * 100) for _ in range(50)]
    total = sum(l.estimate_tokens(m) for m in msgs)
    threshold = int(l._current_model_config["context_window"] * l.SUMMARY_TRIGGER_RATIO)
    assert total <= threshold
    original_len = len(msgs)
    l._summarize_context(msgs)
    assert len(msgs) == original_len


def test_summarize_reduces_messages_on_long_context(_model_ctx_guard):
    """Simulasi 100+ pesan panjang melewati threshold -> panjang berkurang."""
    l = _model_ctx_guard
    _set_model_ctx(l, context_window=16_000)
    msgs = [_umsg("w" * 1000) for _ in range(150)]
    msgs.insert(0, {"role": "system", "content": "system prompt"})

    with patch.object(l, "_call_summary", return_value="RINGKASAN UJI COBA"):
        l._summarize_context(msgs)

    assert len(msgs) < 151
    assert any("[RINGKASAN KONTEKS]" in str(m.get("content", "")) for m in msgs)


def test_summary_retains_context_content(_model_ctx_guard):
    """Ringkasan harus mempertahankan path/file penting."""
    l = _model_ctx_guard
    _set_model_ctx(l, context_window=128_000)
    msgs = [
        {"role": "system", "content": "system"},
        *[_umsg("proyek jalan di /home/user/projek") for _ in range(200)],
    ]

    with patch.object(l, "_call_summary", return_value="Pakai /home/user/projek"):
        l._summarize_context(msgs)

    assert any("/home/user/projek" in str(m.get("content", "")) for m in msgs)


def test_summary_retains_decisions(_model_ctx_guard):
    """Ringkasan harus tetap mempertahankan keputusan penting dari
    percakapan awal meski lama sudah disummarize."""
    l = _model_ctx_guard
    _set_model_ctx(l, context_window=16_000)
    decision_keywords = ["gunakan FastAPI", "jangan lupa validasi input"]
    msgs = [
        {"role": "system", "content": "system prompt"},
        _umsg(f"Keputusan: kami {decision_keywords[0]}."),
        _umsg(f"Juga: {decision_keywords[1]}."),
        *[_umsg("detail lain " * 50) for _ in range(120)],
    ]

    _fake_summary = "; ".join(decision_keywords)
    with patch.object(l, "_call_summary", return_value=_fake_summary):
        l._summarize_context(msgs)

    contents = [str(m.get("content", "")) for m in msgs]
    all_content = " ".join(contents)
    assert decision_keywords[0] in all_content
    assert decision_keywords[1] in all_content


def test_trim_uses_context_window_not_global(_model_ctx_guard):
    """_trim_messages memotong berdasarkan context_window model, bukan MAX_TOKENS."""
    l = _model_ctx_guard
    _set_model_ctx(l, context_window=128_000, max_tokens=4096)
    msgs = [_umsg("y" * 1000) for _ in range(300)]
    trimmed = l._trim_messages([dict(m) for m in msgs], 128_000)
    total = sum(l.estimate_tokens(m) for m in trimmed)
    assert total <= 128_000


def test_small_context_window_trims_more(_model_ctx_guard):
    """Model dengan context_window kecil harus memotong lebih agresif."""
    l = _model_ctx_guard
    msgs = [_umsg("z" * 1000) for _ in range(300)]
    _set_model_ctx(l, context_window=2_000_000)
    big = l._trim_messages([dict(m) for m in msgs], 2_000_000)
    _set_model_ctx(l, context_window=8_000)
    small = l._trim_messages([dict(m) for m in msgs], 8_000)
    assert len(small) <= len(big), "context_window kecil seharusnya memotong lebih banyak"


def test_trim_messages_direct_context_window_below_global_max(_model_ctx_guard):
    """Versi unit-level: context_window eksplisit di bawah MAX_TOKENS global
    tetap dipakai sebagai batas pemotongan oleh _trim_messages (dipanggil
    langsung, bukan lewat call_llm -- lihat test_context_window_overrides_default_max_tokens_for_trim
    di atas untuk versi end-to-end lewat call_llm())."""
    l = _model_ctx_guard
    from joki.constants import MAX_TOKENS

    cw = 5_000
    assert cw < MAX_TOKENS
    _set_model_ctx(l, context_window=cw, max_tokens=4096)

    msgs = [_umsg("a" * 200) for _ in range(200)]
    total_before = sum(l.estimate_tokens(m) for m in msgs)
    assert total_before > cw

    trimmed = l._trim_messages([dict(m) for m in msgs], cw)
    total_after = sum(l.estimate_tokens(m) for m in trimmed)

    assert total_after <= cw
    assert len(trimmed) < len(msgs)


def test_trim_messages_direct_missing_context_window_uses_global_max(_model_ctx_guard):
    """Versi unit-level: tanpa context_window, _trim_messages yang dipanggil
    langsung dengan MAX_TOKENS harus memotong sesuai batas itu (lihat
    test_missing_context_window_falls_back_to_max_tokens_default di atas
    untuk versi end-to-end lewat call_llm())."""
    l = _model_ctx_guard
    from joki.constants import MAX_TOKENS

    _set_model_ctx(l, max_tokens=4096)
    l._current_model_config.pop("context_window", None)
    fallback = l._current_model_config.get("context_window", MAX_TOKENS)
    assert fallback == MAX_TOKENS

    msgs = [_umsg("b" * 200) for _ in range(3_000)]
    total_before = sum(l.estimate_tokens(m) for m in msgs)
    assert total_before > MAX_TOKENS

    trimmed = l._trim_messages([dict(m) for m in msgs], MAX_TOKENS)
    total_after = sum(l.estimate_tokens(m) for m in trimmed)

    assert total_after <= MAX_TOKENS
    assert len(trimmed) < len(msgs)


def test_context_usage_math():
    from joki.llm import _effective_max_tokens, context_usage

    msgs = [
        {"role": "system", "content": "x" * 400},
        {"role": "user", "content": "halo"},
        {"role": "assistant", "content": "hi"},
    ]
    info = context_usage(msgs, [{"function": {"name": "t"}}])
    assert info["messages"] == 3
    assert info["by_role"] == {"system": 108, "user": 8, "assistant": 9}
    assert info["tools"] == 6
    assert info["used"] == 108 + 8 + 9 + 6
    assert info["budget"] == _effective_max_tokens()
    assert info["pct"] == pytest.approx(info["used"] / info["budget"] * 100)


def test_context_usage_empty():
    from joki.llm import context_usage

    info = context_usage([], [])
    assert info == {"used": 0, "budget": info["budget"], "pct": 0.0,
                    "by_role": {}, "tools": 0, "messages": 0}
