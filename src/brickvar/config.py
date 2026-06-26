"""Read JSON config files and resolve their variables.

Variables come from literals, environment variables, and Azure Key Vault secrets
(read through the Databricks ``dbutils.secrets`` API), and are substituted into the
``${VAR}`` placeholders of arbitrary JSON files.
"""

import os
import json
import logging
from string import Template

logger = logging.getLogger(__name__)


def unresolved_variables(content, provided):
    """Variable names referenced as $name/${name} in content that provided does not supply."""
    referenced = {match.group("named") or match.group("braced") for match in Template.pattern.finditer(content)}
    referenced.discard(None)  # escaped ($$) and invalid ($) matches contribute None
    return sorted(referenced - set(provided))


class VariableResolver:  # pylint: disable=too-few-public-methods
    """Reads JSON files and substitutes their ${VAR} placeholders from a variables file."""

    def __init__(self, dbutils=None):
        """Initialize VariableResolver with the Databricks utilities used to read secrets.

        ``dbutils`` is only required when a variables file contains Key Vault secret
        entries; literal- and environment-only files resolve without it.
        """
        self.dbutils = dbutils

    def read_variables(self, filepath: str) -> dict:
        """Read configuration variables from a JSON file.

        Each entry maps a name to a value given in one of four forms: a literal string; JSON
        ``null`` (resolved to ``None``); an environment-variable reference ``{"env": "NAME"}``
        resolved from os.environ; or an Azure Key Vault secret
        ``{"scope": ..., "key": ..., "base"?: ...}`` read via dbutils.

        Resolution is two-pass so entries can reference each other: the first pass resolves the
        literal and environment values, and the second resolves the secrets, substituting any
        ``${VAR}`` in their scope/key/base from the already-resolved first-pass values (e.g. a
        secret whose scope comes from an environment variable). Unknown ``${VAR}`` are left intact,
        so plain literal scopes are unaffected.
        """
        with open(filepath, encoding="utf-8") as f:
            content = json.load(f)
        result = {}

        # First pass: literal, null, and environment values, which secret entries may reference.
        for var_name, var_spec in content.items():
            if isinstance(var_spec, str):
                # Null-valued variables cannot be embedded in a string, so omit them from the
                # cross-reference mapping; their ${VAR} placeholders are left intact here.
                result[var_name] = Template(var_spec).safe_substitute(
                    **{name: value for name, value in result.items() if value is not None}
                )
            elif var_spec is None:
                result[var_name] = None
            elif isinstance(var_spec, dict) and "env" in var_spec:
                extra = set(var_spec) - {"env"}
                if extra:
                    logger.error("Variable %r: env entry has unexpected key(s): %s", var_name, ", ".join(sorted(extra)))
                result[var_name] = os.environ[var_spec["env"]]

        # Second pass: Key Vault secrets, whose scope/key/base may reference first-pass values.
        for var_name, var_spec in content.items():
            if not isinstance(var_spec, dict) or "env" in var_spec:
                continue
            extra = set(var_spec) - {"scope", "key", "base"}
            if extra:
                logger.error("Variable %r: secret entry has unexpected key(s): %s", var_name, ", ".join(sorted(extra)))
            if ("scope" in var_spec) != ("key" in var_spec):
                logger.error("Variable %r: secret entry needs both 'scope' and 'key'; one is missing", var_name)
            scope = var_spec.get("scope")
            key = var_spec.get("key")
            if scope and key:
                resolved = {name: value for name, value in result.items() if value is not None}
                value = self.dbutils.secrets.get(
                    Template(scope).safe_substitute(**resolved),
                    Template(key).safe_substitute(**resolved),
                )
                base = var_spec.get("base")
                result[var_name] = base.format(value) if base else value
        return result

    def read_json(self, filepath: str, var_filepath: str = None) -> dict:
        """Read a JSON file, substituting ${VAR} placeholders from var_filepath when given.

        A variable whose value is ``None`` substitutes a JSON ``null``: its placeholder must be a
        complete JSON string value (``"${VAR}"`` or ``"$VAR"``), which is replaced by the bare
        token ``null``. A null variable embedded in a larger string cannot become null and is left
        intact with a warning. Any placeholder the variables file does not supply is likewise left
        intact and logged as a warning.
        """
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        if var_filepath is not None:
            variables = self.read_variables(var_filepath)
            unresolved = unresolved_variables(content, variables)
            if unresolved:
                logger.warning("Unresolved variable(s) in %s: %s", filepath, ", ".join(unresolved))
            # Replace each null variable's quoted placeholder with a bare JSON null, then
            # textually substitute the remaining (string-valued) variables.
            str_vars = {name: value for name, value in variables.items() if value is not None}
            null_vars = [name for name, value in variables.items() if value is None]
            for name in null_vars:
                content = content.replace(f'"${{{name}}}"', "null").replace(f'"${name}"', "null")
            embedded_nulls = sorted(set(null_vars) & set(unresolved_variables(content, str_vars)))
            if embedded_nulls:
                logger.warning(
                    "Null variable(s) embedded in a string in %s, left unresolved: %s",
                    filepath,
                    ", ".join(embedded_nulls),
                )
            content = Template(content).safe_substitute(**str_vars)
        return json.loads(content)


def configure_json(filepath: str, *, dbutils=None, var_filepath: str = None) -> dict:
    """Read a JSON config file and resolve its ${VAR} placeholders in one call.

    Convenience wrapper that instantiates a VariableResolver with ``dbutils`` and returns
    ``read_json(filepath, var_filepath=var_filepath)``. ``dbutils`` is only needed when the
    variables file contains Key Vault secrets. See VariableResolver.read_json for details.
    """
    return VariableResolver(dbutils=dbutils).read_json(filepath, var_filepath=var_filepath)
