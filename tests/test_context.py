"""Test anti-lupa mendekati max tokens."""
from unittest.mock import patch


def test_messages_budget_subtracts_tools():
    from joki import llm as L
    with patch.object(L, "_effective_max_tokens", return_value=10000), \
         patch.object(L, "_tools_tokens", return_value=1500):
        assert L._messages_budget() == 8500


def test_trim_fifo_preserves_recent():
    from joki.llm import _trim_messages
    msgs = [{"role": "system", "content": "sys"}]
    msgs += [{"role": "user", "content": f"old-{i}-" + "x" * 400} for i in range(5)]
    msgs += [{"role": "user", "content": "RECENT-" + "y" * 400}]
    # Budget kecil paksa trim 1-2 blok tertua.
    _trim_messages(msgs, max_tokens=600)
    texts = " ".join(str(m.get("content", "")) for m in msgs)
    assert "RECENT" in texts
    assert msgs[0]["content"] == "sys"


def test_rebase_clears_stale_read_cache():
    from joki import llm as L
    from joki.state import _read_in_context
    _read_in_context.clear()
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "tool", "content": "HEAD-abc"},
        {"role": "user", "content": "q"},
    ]
    _read_in_context["/tmp/a.py"] = {"hash": "h", "idx": 1, "head": "HEAD-abc"}
    L._rebase_read_cache(msgs)
    assert "/tmp/a.py" in _read_in_context
    # Setelah trim menggeser isi, entri basi harus dibuang.
    msgs2 = [
        {"role": "system", "content": "sys"},
        {"role": "tool", "content": "LAIN"},
        {"role": "user", "content": "q"},
    ]
    L._rebase_read_cache(msgs2) if False else None
    # Simulasi: idx 1 kini berisi konten beda -> dibuang.
    from joki.state import _read_in_context as ric
    ric["/tmp/b.py"] = {"hash": "h", "idx": 1, "head": "HEAD-abc"}
    L._rebase_read_cache(msgs2)
    assert "/tmp/b.py" not in ric
    _read_in_context.clear()


def test_extractive_summary_keeps_request():
    from joki.llm import _extractive_summary
    old = [
        {"role": "user", "content": "buatkan login laravel"},
        {"role": "assistant", "content": "ok"},
        {"role": "system", "content": "[CHECKPOINT TUGAS] Tujuan: login"},
    ]
    s = _extractive_summary(old)
    assert s is not None
    assert "buatkan login" in s or "login" in s


def test_google_no_longer_skips_empty():
    from joki import llm as L
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "A" * 2000},
        {"role": "assistant", "content": "B" * 2000},
        {"role": "user", "content": "C" * 2000},
        {"role": "user", "content": "D" * 2000},
    ]
    cfg = {"provider": "google", "base_url": "https://x", "model": "m", "api_keys": ["k"]}
    with patch.object(L, "_current_model_config", cfg, create=True), \
         patch.object(L, "MAX_TOKENS", 100), \
         patch.object(L, "SUMMARY_WORKING_SET", 2, create=True):
        # Harus menghasilkan ringkasan ekstraktif, bukan skip total.
        L._summarize_context(msgs)
    assert any("RINGKASAN" in str(m.get("content", "")) for m in msgs)


# ============================================================
# A + C-lite: suntikan konteks senyap, system, kaya fakta
# ============================================================

def test_build_context_inject_is_system_with_facts():
    """C-lite: suntikan harus role system + memuat facts/decisions."""
    from joki.cli import _build_context_inject
    ctx = {
        "original_request": "perbaiki login",
        "current_step": "edit auth",
        "files_created": [],
        "files_modified": ["app/Auth.php"],
        "progress_notes": ["test berjalan"],
        "facts": ["DB pakai Postgres 15"],
        "decisions": ["pakai JWT, bukan session"],
    }
    m = _build_context_inject(ctx)
    assert m["role"] == "system"
    c = m["content"]
    assert c.startswith("[KONTEKS TUGAS]")
    assert "Postgres 15" in c
    assert "JWT" in c
    assert "Auth.php" in c


def test_build_context_inject_empty_and_broken_ctx():
    """Ctx kosong/rusak tetap hasilkan pesan system (fail-open)."""
    from joki.cli import _build_context_inject
    m = _build_context_inject({})
    assert m["role"] == "system"
    assert "[KONTEKS TUGAS]" in m["content"]
    m2 = _build_context_inject(None)
    assert m2["role"] == "system"


def test_gemini_system_instruction_merges_all():
    """Kebal Google: semua system digabung, bukan yang terakhir menang."""
    from joki.llm import _gemini_system_instruction
    msgs = [
        {"role": "system", "content": "prompt utama"},
        {"role": "user", "content": "halo"},
        {"role": "system", "content": "[KONTEKS TUGAS] lanjutkan"},
        {"role": "system", "content": "   "},
    ]
    si = _gemini_system_instruction(msgs)
    assert si is not None
    texts = [p["text"] for p in si["parts"]]
    assert texts == ["prompt utama", "[KONTEKS TUGAS] lanjutkan"]
    assert _gemini_system_instruction([{"role": "user", "content": "x"}]) is None


def test_extractive_summary_keeps_konteks():
    """Jalur darurat (google/quota) harus pertahankan suntikan konteks."""
    from joki.llm import _extractive_summary
    old = [
        {"role": "user", "content": "perbaiki login"},
        {"role": "system", "content": "[KONTEKS TUGAS]\nFakta: Postgres 15"},
        {"role": "assistant", "content": "ok"},
    ]
    s = _extractive_summary(old)
    assert s is not None
    assert "Postgres 15" in s


def test_trim_treats_mid_system_as_single_unit():
    """System mid-history boleh di-trim FIFO seperti pesan biasa."""
    from joki.llm import _find_trim_units
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {"role": "system", "content": "[KONTEKS TUGAS] x"},
        {"role": "user", "content": "lanjut"},
    ]
    units = _find_trim_units(msgs)
    # Prefix system dilindungi (mulai dari 1), inject = unit tunggal (2,3).
    assert units[0] == (1, 2)
    assert (2, 3) in units
