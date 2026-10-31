from unittest.mock import MagicMock, patch


@patch("joki.tools.lint.subprocess.run")
def test_run_linter_python_ruff(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="ruff 0.9.0\n", stderr=""
    )
    from joki.tools.lint import handle_run_linter
    result = handle_run_linter({"path": ".", "lang": "python"})
    assert result is not None


@patch("joki.tools.lint.subprocess.run")
def test_run_linter_no_issues(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="", stderr=""
    )
    from joki.tools.lint import handle_run_linter
    result = handle_run_linter({"path": "."})
    assert result is not None


@patch("joki.tools.lint.subprocess.run")
def test_lint_install_status(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="ruff 0.9.0\n", stderr=""
    )
    from joki.tools.lint import handle_lint_install
    result = handle_lint_install({"lang": ""})
    assert result is not None
