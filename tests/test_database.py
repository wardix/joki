from joki.tools.database import _parse_connection


def test_parse_connection_mysql():
    result = _parse_connection("mysql://root:pass@localhost:3306/mydb")
    assert result == ("mysql", "root", "pass", "localhost", "3306", "mydb")

def test_parse_connection_sqlite_absolute():
    result = _parse_connection("sqlite:////tmp/test.db")
    assert result == ("sqlite", "", "", "", "", "/tmp/test.db")

def test_parse_connection_sqlite_relative():
    result = _parse_connection("sqlite:///relative/test.db")
    assert result[0] == "sqlite"
    assert result[5].endswith("/relative/test.db")

def test_parse_connection_postgres():
    result = _parse_connection("postgresql://user:password@remote:5432/dbname")
    assert result == ("postgresql", "user", "password", "remote", "5432", "dbname")
