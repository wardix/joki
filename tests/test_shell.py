import os
import shutil

from joki.tools.shell import (
    _DEFAULT_IDLE_TIMEOUT_MS,
    _FILE_EDIT_PATTERNS,
    _requires_confirmation,
    _run_popen_with_idle,
    handle_predict_command,
    handle_run_command,
    handle_test_and_fix,
)


def test_file_edit_patterns_block_sed():
    assert _FILE_EDIT_PATTERNS.search("sed -i 's/foo/bar/g' file.txt")


def test_file_edit_patterns_block_echo_redirect():
    assert _FILE_EDIT_PATTERNS.search('echo "hello" > file.txt')


def test_file_edit_patterns_block_echo_heredoc():
    assert _FILE_EDIT_PATTERNS.search('echo "hello" >> file.txt')


def test_file_edit_patterns_block_awk_redirect():
    assert _FILE_EDIT_PATTERNS.search("awk '{print $1}' > out.txt")


def test_file_edit_patterns_block_cat_redirect():
    assert _FILE_EDIT_PATTERNS.search("cat file1 file2 > merged.txt")


def test_file_edit_patterns_allow_safe_ls():
    assert not _FILE_EDIT_PATTERNS.search("ls -la")


def test_file_edit_patterns_allow_safe_grep():
    assert not _FILE_EDIT_PATTERNS.search("grep pattern file.txt")


def test_run_command_block_edit_sed():
    result = handle_run_command({"cmd": "sed -i 's/foo/bar/g' file.txt"})
    assert "BLOKIR" in result
    assert "run_command" in result


def test_run_command_block_edit_echo():
    result = handle_run_command({"cmd": "echo 'test' > /tmp/x.txt"})
    assert "BLOKIR" in result


def test_run_command_empty_cmd():
    result = handle_run_command({"cmd": ""})
    assert "wajib diisi" in result


def test_predict_command_dangerous_rm():
    result = handle_predict_command({"cmd": "rm -rf /"})
    assert "⚠" in result


def test_predict_command_safe_ls():
    result = handle_predict_command({"cmd": "ls -la"})
    assert "✓" in result


def test_run_command_identity_target_blocked():
    result = handle_run_command({"cmd": "sed -i 's/a/b/g' /etc/shadow"})
    assert "DILARANG" in result
    result = handle_run_command({"cmd": "echo 'x' > /etc/sudoers"})
    assert "DILARANG" in result


def test_run_command_non_identity_edit_still_generic_block():
    result = handle_run_command({"cmd": "sed -i 's/a/b/g' /tmp/project/app.conf"})
    assert "BLOKIR" in result


def test_run_command_destructive_rm_requires_confirmation():
    target = "/tmp/joki_p1_rm_target"
    os.makedirs(target, exist_ok=True)
    marker = os.path.join(target, "marker.txt")
    with open(marker, "w") as f:
        f.write("keep")
    try:
        result = handle_run_command({"cmd": f"rm -rf {target}"})
        assert "KONFIRMASI" in result
        assert os.path.exists(target)
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_run_command_destructive_rm_with_confirmation():
    target = "/tmp/joki_p1_rm_target"
    os.makedirs(target, exist_ok=True)
    try:
        handle_run_command({"cmd": f"rm -rf {target}", "confirmed": True})
        assert not os.path.exists(target)
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_run_command_safe_no_confirmation_needed():
    result = handle_run_command({"cmd": "echo joki_safe_123"})
    assert "KONFIRMASI" not in result
    assert "joki_safe_123" in result


def test_requires_confirmation_rm_variants():
    assert _requires_confirmation("rm -rf /some/path")[0] is True
    assert _requires_confirmation("rm -fr /etc")[0] is True
    assert _requires_confirmation("sudo rm -rf ~/data")[0] is True
    assert _requires_confirmation("rm file.txt")[0] is False
    assert _requires_confirmation("rm -r build")[0] is False


def test_requires_confirmation_db():
    assert _requires_confirmation("DROP TABLE users")[0] is True
    assert _requires_confirmation("TRUNCATE TABLE logs")[0] is True
    assert _requires_confirmation("SELECT * FROM users")[0] is False


def test_requires_confirmation_dd():
    assert _requires_confirmation("dd if=/dev/zero of=/dev/sda bs=1M")[0] is True
    assert _requires_confirmation("dd if=/dev/zero of=/tmp/test.img bs=1M count=1")[0] is False


def test_requires_confirmation_misc():
    assert _requires_confirmation("mkfs.ext4 /dev/sdb1")[0] is True
    assert _requires_confirmation(":(){ :|:& };:")[0] is True
    assert _requires_confirmation("chmod -R 777 /")[0] is True
    assert _requires_confirmation("chmod 755 file.sh")[0] is False
    assert _requires_confirmation("reboot -f")[0] is True
    assert _requires_confirmation("shutdown -h now")[0] is True
    assert _requires_confirmation("ls -la")[0] is False


def test_handle_test_and_fix_runs_with_spinner():
    result = handle_test_and_fix({"cmd": "echo hi"})
    assert "SUCCESS" in result


def test_default_idle_timeout_is_60s():
    assert _DEFAULT_IDLE_TIMEOUT_MS == 60_000


def test_run_command_stuck_on_idle_input():
    result = handle_run_command({"cmd": "read x", "timeout": 5000, "idle_timeout": 500})
    # Command menunggu input tanpa output → harus timeout/stuck dengan pesan
    # yang menunjukkan command tidak menghasilkan output.
    assert result.strip()  # hasil tidak kosong
    assert ("[STUCK]" in result or "Command timeout" in result or "timeout" in result.lower())


def test_run_command_normal_command_not_stuck():
    result = handle_run_command({"cmd": "echo joki_ok_123", "idle_timeout": 1000})
    assert "[STUCK]" not in result
    assert "joki_ok_123" in result


def test_run_command_idle_timeout_zero_disables():
    result = handle_run_command({"cmd": "echo joki_zero_ok", "idle_timeout": 0})
    assert "[STUCK]" not in result
    assert "joki_zero_ok" in result


def test_run_command_idle_timeout_clamped_to_timeout():
    result = handle_run_command({"cmd": "echo joki_clamped_ok", "timeout": 2000, "idle_timeout": 999999})
    assert "[STUCK]" not in result
    assert "joki_clamped_ok" in result


def test_run_popen_with_idle_stuck_on_read():
    result = _run_popen_with_idle("read x", timeout=5, idle_timeout=1)
    assert "[STUCK]" in result


def test_run_popen_with_idle_normal():
    result = _run_popen_with_idle("echo joki_fallback_ok", timeout=5, idle_timeout=1)
    assert "[STUCK]" not in result
    assert "joki_fallback_ok" in result
