"""Tests for brickvar.config.ConfigManager."""

from brickvar import ConfigManager
from brickvar.config import unresolved_variables


def test_read_variables_literal_and_cross_reference(mock_dbutils, write_json):
    """Literal entries resolve as-is, and a later literal may reference an earlier one."""
    var_path = write_json("vars.json", {"BASE": "root", "CHILD": "${BASE}/child"})

    result = ConfigManager(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"BASE": "root", "CHILD": "root/child"}


def test_read_variables_from_environment(mock_dbutils, monkeypatch, write_json):
    """An {"env": NAME} entry resolves from an environment variable."""
    monkeypatch.setenv("VASRD_PATH", "abfss://vasrd@account/current")
    var_path = write_json("vars.json", {"VASRD_PATH": {"env": "VASRD_PATH"}})

    result = ConfigManager(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"VASRD_PATH": "abfss://vasrd@account/current"}


def test_read_variables_secret_with_base(mock_dbutils, write_json):
    """A secret entry reads via dbutils and applies the optional base format string."""
    var_path = write_json(
        "vars.json",
        {
            "PLAIN": {"scope": "s", "key": "k"},
            "WRAPPED": {"scope": "s", "key": "k", "base": "abfss://ci@{}/zero"},
        },
    )

    result = ConfigManager(dbutils=mock_dbutils).read_variables(var_path)

    mock_dbutils.secrets.get.assert_any_call("s", "k")
    assert result["PLAIN"] == "s_k_value"
    assert result["WRAPPED"] == "abfss://ci@s_k_value/zero"


def test_read_variables_secret_scope_from_environment(mock_dbutils, monkeypatch, write_json):
    """A secret entry's scope can reference an env-backed variable (two-pass resolution).

    SCOPE is declared after the secret that uses it, confirming resolution is order-independent.
    """
    monkeypatch.setenv("SECRET_SCOPE", "NeoSecretScope")
    var_path = write_json(
        "vars.json",
        {
            "URL": {"scope": "${SCOPE}", "key": "STORAGE-URL"},
            "SCOPE": {"env": "SECRET_SCOPE"},
        },
    )

    result = ConfigManager(dbutils=mock_dbutils).read_variables(var_path)

    # The scope ${SCOPE} resolved to the env value before the secret was fetched.
    mock_dbutils.secrets.get.assert_any_call("NeoSecretScope", "STORAGE-URL")
    assert result["URL"] == "NeoSecretScope_STORAGE-URL_value"
    assert result["SCOPE"] == "NeoSecretScope"


def test_read_variables_skips_incomplete_secret(mock_dbutils, write_json):
    """A dict missing scope or key is not treated as a secret and is left out of the result."""
    var_path = write_json(
        "vars.json",
        {"NO_KEY": {"scope": "s"}, "NO_SCOPE": {"key": "k"}, "GOOD": "literal"},
    )

    result = ConfigManager(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"GOOD": "literal"}
    mock_dbutils.secrets.get.assert_not_called()


def test_read_variables_logs_error_for_env_entry_with_extra_key(mock_dbutils, monkeypatch, write_json, mocker):
    """An env entry with any key beyond 'env' logs an error but still resolves from the environment."""
    monkeypatch.setenv("HOST", "example.com")
    var_path = write_json("vars.json", {"HOST": {"env": "HOST", "scope": "oops"}})
    error = mocker.patch("brickvar.config.logger.error")

    result = ConfigManager(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"HOST": "example.com"}
    error.assert_called_once()
    assert "scope" in error.call_args.args[-1]


def test_read_variables_logs_error_for_secret_entry_with_unexpected_key(mock_dbutils, write_json, mocker):
    """A secret entry with keys outside scope/key/base logs an error but still resolves."""
    var_path = write_json("vars.json", {"S": {"scope": "s", "key": "k", "bogus": 1}})
    error = mocker.patch("brickvar.config.logger.error")

    result = ConfigManager(dbutils=mock_dbutils).read_variables(var_path)

    assert result["S"] == "s_k_value"
    error.assert_called_once()
    assert "bogus" in error.call_args.args[-1]


def test_read_variables_logs_error_for_unpaired_scope_or_key(mock_dbutils, write_json, mocker):
    """A secret entry with only one of scope/key logs an error and is not fetched."""
    var_path = write_json("vars.json", {"NO_KEY": {"scope": "s"}, "NO_SCOPE": {"key": "k"}})
    error = mocker.patch("brickvar.config.logger.error")

    result = ConfigManager(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {}
    assert error.call_count == 2
    mock_dbutils.secrets.get.assert_not_called()


def test_read_variables_logs_no_error_for_valid_entries(mock_dbutils, write_json, mocker):
    """Well-formed literal, env, and secret (with optional base) entries produce no error logs."""
    var_path = write_json(
        "vars.json",
        {
            "LIT": "x",
            "PLAIN": {"scope": "s", "key": "k"},
            "WRAPPED": {"scope": "s", "key": "k", "base": "ci@{}"},
        },
    )
    error = mocker.patch("brickvar.config.logger.error")

    ConfigManager(dbutils=mock_dbutils).read_variables(var_path)

    error.assert_not_called()


def test_read_json_substitutes_variables(mock_dbutils, write_json):
    """read_json substitutes ${VAR} placeholders from the variables file."""
    var_path = write_json("vars.json", {"HOST": "example.com", "DB": "grads"})
    doc_path = write_json("doc.json", {"endpoint": "https://${HOST}", "database": "${DB}"})

    result = ConfigManager(dbutils=mock_dbutils).read_json(doc_path, var_path)

    assert result == {"endpoint": "https://example.com", "database": "grads"}


def test_read_json_without_variables(mock_dbutils, write_json):
    """read_json with no variables file returns the JSON unchanged."""
    doc_path = write_json("doc.json", {"a": 1, "b": "${UNTOUCHED}"})

    result = ConfigManager(dbutils=mock_dbutils).read_json(doc_path)

    assert result == {"a": 1, "b": "${UNTOUCHED}"}


def test_read_json_warns_on_unresolved_variable(mock_dbutils, write_json, mocker):
    """A ${VAR} the variables file does not supply is left intact and logged as a warning."""
    var_path = write_json("vars.json", {"PROVIDED": "ok"})
    doc_path = write_json("doc.json", {"path": "${PROVIDED}", "extra": "${MISSING}"})
    warning = mocker.patch("brickvar.config.logger.warning")

    result = ConfigManager(dbutils=mock_dbutils).read_json(doc_path, var_path)

    assert result == {"path": "ok", "extra": "${MISSING}"}
    warning.assert_called_once()
    # The warning names the unresolved variable, not the resolved one.
    assert "MISSING" in warning.call_args.args[-1]
    assert "PROVIDED" not in warning.call_args.args[-1]


def test_unresolved_variables_helper():
    """unresolved_variables reports referenced names absent from provided, ignoring escapes."""
    content = "${A} and $B and $$C and ${D}"
    assert unresolved_variables(content, {"A": 1}) == ["B", "D"]
