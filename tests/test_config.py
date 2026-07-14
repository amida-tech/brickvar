"""Tests for brickvar.config.VariableResolver."""

import pytest

from brickvar import VariableResolver, configure_json, configure_jsons
from brickvar.config import unresolved_variables


def test_read_variables_literal_and_cross_reference(mock_dbutils, write_json):
    """Literal entries resolve as-is, and a later literal may reference an earlier one."""
    var_path = write_json("vars.json", {"BASE": "root", "CHILD": "${BASE}/child"})

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"BASE": "root", "CHILD": "root/child"}


def test_read_variables_from_environment(mock_dbutils, monkeypatch, write_json):
    """An {"env": NAME} entry resolves from an environment variable."""
    monkeypatch.setenv("VASRD_PATH", "abfss://vasrd@account/current")
    var_path = write_json("vars.json", {"VASRD_PATH": {"env": "VASRD_PATH"}})

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

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

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

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

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    # The scope ${SCOPE} resolved to the env value before the secret was fetched.
    mock_dbutils.secrets.get.assert_any_call("NeoSecretScope", "STORAGE-URL")
    assert result["URL"] == "NeoSecretScope_STORAGE-URL_value"
    assert result["SCOPE"] == "NeoSecretScope"


def test_read_variables_raises_on_incomplete_secret(mock_dbutils, write_json):
    """A secret entry with scope but no key raises ValueError and is not fetched."""
    var_path = write_json("vars.json", {"NO_KEY": {"scope": "s"}})

    with pytest.raises(ValueError, match="needs both 'scope' and 'key'"):
        VariableResolver(dbutils=mock_dbutils).read_variables(var_path)
    mock_dbutils.secrets.get.assert_not_called()


def test_read_variables_logs_error_for_env_entry_with_extra_key(mock_dbutils, monkeypatch, write_json, mocker):
    """An env entry with any key beyond 'env' logs an error but still resolves from the environment."""
    monkeypatch.setenv("HOST", "example.com")
    var_path = write_json("vars.json", {"HOST": {"env": "HOST", "scope": "oops"}})
    error = mocker.patch("brickvar.config.logger.error")

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"HOST": "example.com"}
    error.assert_called_once()
    assert "scope" in error.call_args.args[-1]


def test_read_variables_logs_error_for_secret_entry_with_unexpected_key(mock_dbutils, write_json, mocker):
    """A secret entry with keys outside scope/key/base logs an error but still resolves."""
    var_path = write_json("vars.json", {"S": {"scope": "s", "key": "k", "bogus": 1}})
    error = mocker.patch("brickvar.config.logger.error")

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    assert result["S"] == "s_k_value"
    error.assert_called_once()
    assert "bogus" in error.call_args.args[-1]


def test_read_variables_raises_on_unpaired_key_without_scope(mock_dbutils, write_json):
    """A secret entry with key but no scope raises ValueError and is not fetched."""
    var_path = write_json("vars.json", {"NO_SCOPE": {"key": "k"}})

    with pytest.raises(ValueError, match="needs both 'scope' and 'key'"):
        VariableResolver(dbutils=mock_dbutils).read_variables(var_path)
    mock_dbutils.secrets.get.assert_not_called()


def test_read_variables_raises_on_env_and_seq(mock_dbutils, write_json):
    """An entry with both 'env' and 'seq' is ambiguous and raises ValueError."""
    var_path = write_json("vars.json", {"X": {"env": "HOME", "seq": "s{i}", "count": 1}})

    with pytest.raises(ValueError, match="both 'env' and 'seq'"):
        VariableResolver(dbutils=mock_dbutils).read_variables(var_path)


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

    VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    error.assert_not_called()


def test_read_variables_null(mock_dbutils, write_json):
    """A JSON null entry resolves to None."""
    var_path = write_json("vars.json", {"OPT": None, "LIT": "x"})

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"OPT": None, "LIT": "x"}


def test_read_variables_seq_zero_padded(mock_dbutils, write_json):
    """A seq entry expands to a delimited string, its {i} counter honoring the format spec."""
    var_path = write_json("vars.json", {"ABD_IDS": {"seq": "ABD{i:02d}", "count": 3}})

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"ABD_IDS": "ABD01, ABD02, ABD03"}


def test_read_variables_seq_defaults(mock_dbutils, write_json):
    """seq defaults to start 1 and separator ", " with no padding on a bare {i}."""
    var_path = write_json("vars.json", {"CDE_IDS": {"seq": "CDE{i}", "count": 3}})

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"CDE_IDS": "CDE1, CDE2, CDE3"}


def test_read_variables_seq_custom_start_step_sep(mock_dbutils, write_json):
    """seq honors explicit start, step, and separator."""
    var_path = write_json(
        "vars.json",
        {"IDS": {"seq": "N{i}", "start": 10, "step": 5, "count": 3, "sep": "|"}},
    )

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"IDS": "N10|N15|N20"}


def test_read_variables_seq_count_zero(mock_dbutils, write_json):
    """A seq with count 0 expands to an empty string."""
    var_path = write_json("vars.json", {"IDS": {"seq": "X{i}", "count": 0}})

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"IDS": ""}


def test_read_variables_seq_references_earlier_variable(mock_dbutils, write_json):
    """A seq template may reference an earlier-resolved variable via ${VAR}."""
    var_path = write_json(
        "vars.json",
        {"PREFIX": "ABD", "IDS": {"seq": "${PREFIX}{i:02d}", "count": 2}},
    )

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    assert result["IDS"] == "ABD01, ABD02"


def test_read_variables_seq_requires_count(mock_dbutils, write_json):
    """A seq entry without count raises ValueError."""
    var_path = write_json("vars.json", {"IDS": {"seq": "X{i}"}})

    with pytest.raises(ValueError, match="requires a 'count'"):
        VariableResolver(dbutils=mock_dbutils).read_variables(var_path)


def test_read_variables_seq_negative_count_raises(mock_dbutils, write_json):
    """A seq with a negative count raises ValueError."""
    var_path = write_json("vars.json", {"IDS": {"seq": "X{i}", "count": -1}})

    with pytest.raises(ValueError, match="'count' must be non-negative"):
        VariableResolver(dbutils=mock_dbutils).read_variables(var_path)


def test_read_variables_seq_negative_step_raises(mock_dbutils, write_json):
    """A seq with a negative step raises ValueError."""
    var_path = write_json("vars.json", {"IDS": {"seq": "X{i}", "count": 2, "step": -1}})

    with pytest.raises(ValueError, match="'step' must be non-negative"):
        VariableResolver(dbutils=mock_dbutils).read_variables(var_path)


def test_read_variables_seq_logs_error_for_unexpected_key(mock_dbutils, write_json, mocker):
    """A seq entry with an unexpected key logs an error but still resolves."""
    var_path = write_json("vars.json", {"IDS": {"seq": "X{i}", "count": 2, "bogus": 1}})
    error = mocker.patch("brickvar.config.logger.error")

    result = VariableResolver(dbutils=mock_dbutils).read_variables(var_path)

    assert result == {"IDS": "X1, X2"}
    error.assert_called_once()
    assert "bogus" in error.call_args.args[-1]


def test_read_json_substitutes_seq(mock_dbutils, write_json):
    """A seq variable substitutes its delimited string into a ${VAR} placeholder."""
    var_path = write_json("vars.json", {"COLUMNS": {"seq": "col{i}", "count": 3}})
    doc_path = write_json("doc.json", {"select": "${COLUMNS}"})

    result = VariableResolver(dbutils=mock_dbutils).read_json(doc_path, var_path)

    assert result == {"select": "col1, col2, col3"}


def test_read_json_substitutes_null(mock_dbutils, write_json):
    """A null variable replaces a complete "${VAR}" string value with a JSON null."""
    var_path = write_json("vars.json", {"DB": None, "HOST": "example.com"})
    doc_path = write_json("doc.json", {"database": "${DB}", "endpoint": "https://${HOST}"})

    result = VariableResolver(dbutils=mock_dbutils).read_json(doc_path, var_path)

    assert result == {"database": None, "endpoint": "https://example.com"}


def test_read_json_substitutes_null_unbraced(mock_dbutils, write_json):
    """The unbraced "$VAR" form also becomes a JSON null."""
    var_path = write_json("vars.json", {"DB": None})
    doc_path = write_json("doc.json", {"database": "$DB"})

    result = VariableResolver(dbutils=mock_dbutils).read_json(doc_path, var_path)

    assert result == {"database": None}


def test_read_json_null_alongside_secret(mock_dbutils, write_json):
    """A null variable and a secret resolve together in one document."""
    var_path = write_json("vars.json", {"OPT": None, "TOKEN": {"scope": "s", "key": "k"}})
    doc_path = write_json("doc.json", {"optional": "${OPT}", "token": "${TOKEN}"})

    result = VariableResolver(dbutils=mock_dbutils).read_json(doc_path, var_path)

    assert result == {"optional": None, "token": "s_k_value"}


def test_read_json_null_no_unresolved_warning(mock_dbutils, write_json, mocker):
    """A null variable counts as provided, so it triggers no unresolved-variable warning."""
    var_path = write_json("vars.json", {"DB": None})
    doc_path = write_json("doc.json", {"database": "${DB}"})
    warning = mocker.patch("brickvar.config.logger.warning")

    result = VariableResolver(dbutils=mock_dbutils).read_json(doc_path, var_path)

    assert result == {"database": None}
    warning.assert_not_called()


def test_read_json_null_embedded_in_string_warns(mock_dbutils, write_json, mocker):
    """A null variable embedded in a larger string cannot become null and is left intact with a warning."""
    var_path = write_json("vars.json", {"DB": None})
    doc_path = write_json("doc.json", {"path": "prefix-${DB}"})
    warning = mocker.patch("brickvar.config.logger.warning")

    result = VariableResolver(dbutils=mock_dbutils).read_json(doc_path, var_path)

    assert result == {"path": "prefix-${DB}"}
    warning.assert_called_once()
    assert "DB" in warning.call_args.args[-1]


def test_read_json_substitutes_variables(mock_dbutils, write_json):
    """read_json substitutes ${VAR} placeholders from the variables file."""
    var_path = write_json("vars.json", {"HOST": "example.com", "DB": "grads"})
    doc_path = write_json("doc.json", {"endpoint": "https://${HOST}", "database": "${DB}"})

    result = VariableResolver(dbutils=mock_dbutils).read_json(doc_path, var_path)

    assert result == {"endpoint": "https://example.com", "database": "grads"}


def test_read_json_without_variables(mock_dbutils, write_json):
    """read_json with no variables file returns the JSON unchanged."""
    doc_path = write_json("doc.json", {"a": 1, "b": "${UNTOUCHED}"})

    result = VariableResolver(dbutils=mock_dbutils).read_json(doc_path)

    assert result == {"a": 1, "b": "${UNTOUCHED}"}


def test_read_json_warns_on_unresolved_variable(mock_dbutils, write_json, mocker):
    """A ${VAR} the variables file does not supply is left intact and logged as a warning."""
    var_path = write_json("vars.json", {"PROVIDED": "ok"})
    doc_path = write_json("doc.json", {"path": "${PROVIDED}", "extra": "${MISSING}"})
    warning = mocker.patch("brickvar.config.logger.warning")

    result = VariableResolver(dbutils=mock_dbutils).read_json(doc_path, var_path)

    assert result == {"path": "ok", "extra": "${MISSING}"}
    warning.assert_called_once()
    # The warning names the unresolved variable, not the resolved one.
    assert "MISSING" in warning.call_args.args[-1]
    assert "PROVIDED" not in warning.call_args.args[-1]


def test_configure_json_resolves_with_variables(mock_dbutils, write_json):
    """configure_json reads the file and substitutes ${VAR} placeholders in one call."""
    var_path = write_json("vars.json", {"HOST": "example.com", "DB": "grads"})
    doc_path = write_json("doc.json", {"endpoint": "https://${HOST}", "database": "${DB}"})

    result = configure_json(doc_path, dbutils=mock_dbutils, var_filepath=var_path)

    assert result == {"endpoint": "https://example.com", "database": "grads"}


def test_configure_json_without_variables(write_json):
    """configure_json with no variables file (and no dbutils) returns the JSON unchanged."""
    doc_path = write_json("doc.json", {"a": 1, "b": "${UNTOUCHED}"})

    result = configure_json(doc_path)

    assert result == {"a": 1, "b": "${UNTOUCHED}"}


def test_configure_json_resolves_secret(mock_dbutils, write_json):
    """configure_json passes dbutils through to VariableResolver so Key Vault secrets resolve."""
    var_path = write_json("vars.json", {"TOKEN": {"scope": "s", "key": "k"}})
    doc_path = write_json("doc.json", {"token": "${TOKEN}"})

    result = configure_json(doc_path, dbutils=mock_dbutils, var_filepath=var_path)

    assert result == {"token": "s_k_value"}


def test_read_jsons_merges_and_substitutes(mock_dbutils, write_json):
    """read_jsons merges several config files and substitutes merged variables."""
    var_path = write_json("vars.json", {"HOST": "example.com"})
    a_path = write_json("a.json", {"endpoint": "https://${HOST}"})
    b_path = write_json("b.json", {"db": "grads"})

    result = VariableResolver(dbutils=mock_dbutils).read_jsons([a_path, b_path], var_filepaths=[var_path])

    assert result == {"endpoint": "https://example.com", "db": "grads"}


def test_configure_jsons_merges_distinct_keys(mock_dbutils, write_json):
    """configure_jsons merges the distinct keys of several config files into one object."""
    var_path = write_json("vars.json", {"HOST": "example.com"})
    a_path = write_json("a.json", {"endpoint": "https://${HOST}"})
    b_path = write_json("b.json", {"db": "grads"})

    result = configure_jsons([a_path, b_path], dbutils=mock_dbutils, var_filepaths=[var_path])

    assert result == {"endpoint": "https://example.com", "db": "grads"}


def test_configure_jsons_later_file_wins_on_conflict(write_json, mocker):
    """A top-level key defined by more than one file takes the last file's value, with a warning."""
    a_path = write_json("a.json", {"port": 1, "host": "a"})
    b_path = write_json("b.json", {"port": 2})
    warning = mocker.patch("brickvar.config.logger.warning")

    result = configure_jsons([a_path, b_path])

    assert result == {"port": 2, "host": "a"}
    warning.assert_called_once()
    assert "port" in warning.call_args.args[0] % warning.call_args.args[1:]


def test_configure_jsons_deep_merges_nested_object(write_json):
    """Merge is deep: a conflicting top-level object is merged key by key, not replaced wholesale."""
    a_path = write_json("a.json", {"db": {"host": "h", "port": 1}})
    b_path = write_json("b.json", {"db": {"port": 2}})

    result = configure_jsons([a_path, b_path])

    assert result == {"db": {"host": "h", "port": 2}}


def test_configure_jsons_appends_lists(write_json):
    """Lists at the same key are concatenated (later file's items appended), not replaced."""
    a_path = write_json("a.json", {"steps": ["a", "b"]})
    b_path = write_json("b.json", {"steps": ["c"]})

    result = configure_jsons([a_path, b_path])

    assert result == {"steps": ["a", "b", "c"]}


def test_configure_jsons_deep_merges_recursively(write_json):
    """Deep merge descends through nested objects, appending lists and last-wins on scalars."""
    a_path = write_json("a.json", {"job": {"tags": ["x"], "cfg": {"retries": 1, "timeout": 30}}})
    b_path = write_json("b.json", {"job": {"tags": ["y"], "cfg": {"retries": 3}}, "extra": True})

    result = configure_jsons([a_path, b_path])

    assert result == {"job": {"tags": ["x", "y"], "cfg": {"retries": 3, "timeout": 30}}, "extra": True}


def test_configure_jsons_raises_on_conflicting_types(write_json):
    """A key given incompatible shapes by two files (list vs object) raises ValueError."""
    a_path = write_json("a.json", {"db": ["a", "b"]})
    b_path = write_json("b.json", {"db": {"host": "h"}})

    with pytest.raises(ValueError, match="Conflicting types for 'db'"):
        configure_jsons([a_path, b_path])


def test_configure_jsons_null_overrides_container_with_warning(mock_dbutils, write_json, mocker):
    """A JSON null opposite a container is a last-wins override (not a raise), logged as a warning."""
    var_path = write_json("vars.json", {"NONE": None})
    a_path = write_json("a.json", {"db": {"host": "h"}})
    b_path = write_json("b.json", {"db": "${NONE}"})
    warning = mocker.patch("brickvar.config.logger.warning")

    result = configure_jsons([a_path, b_path], dbutils=mock_dbutils, var_filepaths=[var_path])

    assert result == {"db": None}
    warning.assert_called_once()
    assert "db" in warning.call_args.args[0] % warning.call_args.args[1:]


def test_configure_jsons_container_overrides_null_with_warning(mock_dbutils, write_json, mocker):
    """A container opposite an earlier null wins as a last-wins override, logged as a warning."""
    var_path = write_json("vars.json", {"NONE": None})
    a_path = write_json("a.json", {"db": "${NONE}"})
    b_path = write_json("b.json", {"db": {"host": "h"}})
    warning = mocker.patch("brickvar.config.logger.warning")

    result = configure_jsons([a_path, b_path], dbutils=mock_dbutils, var_filepaths=[var_path])

    assert result == {"db": {"host": "h"}}
    warning.assert_called_once()
    assert "db" in warning.call_args.args[0] % warning.call_args.args[1:]


def test_configure_jsons_list_append_logs_info(write_json, mocker):
    """Appending lists is non-lossy, so it is logged at INFO rather than as a warning."""
    a_path = write_json("a.json", {"steps": ["a"]})
    b_path = write_json("b.json", {"steps": ["b"]})
    info = mocker.patch("brickvar.config.logger.info")
    warning = mocker.patch("brickvar.config.logger.warning")

    configure_jsons([a_path, b_path])

    info.assert_called_once()
    assert "steps" in info.call_args.args[0] % info.call_args.args[1:]
    warning.assert_not_called()


def test_configure_jsons_identical_scalar_logs_info(write_json, mocker):
    """Redefining a scalar with an equal value discards nothing, so it is logged at INFO."""
    a_path = write_json("a.json", {"port": 5})
    b_path = write_json("b.json", {"port": 5})
    info = mocker.patch("brickvar.config.logger.info")
    warning = mocker.patch("brickvar.config.logger.warning")

    result = configure_jsons([a_path, b_path])

    assert result == {"port": 5}
    info.assert_called_once()
    warning.assert_not_called()


def test_configure_jsons_merges_variables_across_files(mock_dbutils, write_json):
    """Variables files are merged before resolution, so one file may reference another's variable."""
    base_path = write_json("base.vars.json", {"BASE": "root"})
    env_path = write_json("env.vars.json", {"CHILD": "${BASE}/child"})
    doc_path = write_json("doc.json", {"path": "${CHILD}"})

    result = configure_jsons([doc_path], dbutils=mock_dbutils, var_filepaths=[base_path, env_path])

    assert result == {"path": "root/child"}


def test_configure_jsons_later_variable_file_wins(mock_dbutils, write_json, mocker):
    """A variable defined in more than one file takes the last file's definition, with a warning."""
    base_path = write_json("base.vars.json", {"HOST": "base.example.com"})
    override_path = write_json("override.vars.json", {"HOST": "prod.example.com"})
    doc_path = write_json("doc.json", {"endpoint": "https://${HOST}"})
    warning = mocker.patch("brickvar.config.logger.warning")

    result = configure_jsons([doc_path], dbutils=mock_dbutils, var_filepaths=[base_path, override_path])

    assert result == {"endpoint": "https://prod.example.com"}
    warning.assert_called_once()
    assert "HOST" in warning.call_args.args[0] % warning.call_args.args[1:]


def test_configure_jsons_without_variables(write_json):
    """configure_jsons with no variables files (and no dbutils) merges the files unchanged."""
    a_path = write_json("a.json", {"a": 1})
    b_path = write_json("b.json", {"b": "${UNTOUCHED}"})

    result = configure_jsons([a_path, b_path])

    assert result == {"a": 1, "b": "${UNTOUCHED}"}


def test_configure_jsons_substitutes_null_across_files(mock_dbutils, write_json):
    """A null variable resolves to JSON null in whichever merged file references it."""
    var_path = write_json("vars.json", {"OPT": None})
    a_path = write_json("a.json", {"optional": "${OPT}"})
    b_path = write_json("b.json", {"name": "fixed"})

    result = configure_jsons([a_path, b_path], dbutils=mock_dbutils, var_filepaths=[var_path])

    assert result == {"optional": None, "name": "fixed"}


def test_configure_jsons_raises_on_non_object_config(mock_dbutils, write_json):
    """A config file whose top level is not a JSON object raises ValueError."""
    a_path = write_json("a.json", {"ok": 1})
    b_path = write_json("b.json", ["not", "an", "object"])

    with pytest.raises(ValueError, match="not a JSON object"):
        configure_jsons([a_path, b_path], dbutils=mock_dbutils)


def test_unresolved_variables_helper():
    """unresolved_variables reports referenced names absent from provided, ignoring escapes."""
    content = "${A} and $B and $$C and ${D}"
    assert unresolved_variables(content, {"A": 1}) == ["B", "D"]
