"""Read JSON config files and resolve their variables.

Variables come from literals, environment variables, and Azure Key Vault secrets
(read through the Databricks ``dbutils.secrets`` API), and are substituted into the
``${VAR}`` placeholders of arbitrary JSON files.
"""

from __future__ import annotations

import os
import json
import logging
from collections.abc import Iterable
from string import Template

logger = logging.getLogger(__name__)


def unresolved_variables(content: str, provided: Iterable[str]) -> list[str]:
    """Variable names referenced as $name/${name} in content that provided does not supply."""
    referenced = {match.group("named") or match.group("braced") for match in Template.pattern.finditer(content)}
    referenced.discard(None)  # escaped ($$) and invalid ($) matches contribute None
    return sorted(referenced - set(provided))


class VariableResolver:
    """Reads JSON files and substitutes their ${VAR} placeholders from a variables file."""

    def __init__(self, dbutils=None):
        """Initialize VariableResolver with the Databricks utilities used to read secrets.

        ``dbutils`` is only required when a variables file contains Key Vault secret
        entries; literal- and environment-only files resolve without it.
        """
        self.dbutils = dbutils

    def read_variables(self, filepath: str) -> dict:
        """Read configuration variables from a JSON file.

        Each entry maps a name to a value given in one of five forms: a literal string; JSON
        ``null`` (resolved to ``None``); an environment-variable reference ``{"env": "NAME"}``
        resolved from os.environ; an Azure Key Vault secret
        ``{"scope": ..., "key": ..., "base"?: ...}`` read via dbutils; or a counter sequence
        ``{"seq": ..., "count": ..., "start"?: ..., "step"?: ..., "sep"?: ..., "as"?: ...}``
        expanded to a single delimited string, or to a list of strings when ``as`` is
        ``"array"`` (see _resolve_seq).

        Resolution is two-pass so entries can reference each other: the first pass resolves the
        literal and environment values, and the second resolves the secrets, substituting any
        ``${VAR}`` in their scope/key/base from the already-resolved first-pass values (e.g. a
        secret whose scope comes from an environment variable). Unknown ``${VAR}`` are left intact,
        so plain literal scopes are unaffected.
        """
        with open(filepath, encoding="utf-8") as f:
            content = json.load(f)
        return self._resolve_variables(content)

    def _resolve_variables(self, content: dict) -> dict:
        """Resolve a mapping of variable specs (see read_variables) to concrete values.

        Split out from read_variables so callers that merge several variables files can
        combine the raw specs first and resolve the merged mapping in a single two-pass run,
        which is what lets a variable in one file reference one defined in another.
        """
        result = {}

        # First pass: literal, null, and environment values, which secret entries may reference.
        for var_name, var_spec in content.items():
            if isinstance(var_spec, str):
                # Only string values can be embedded in another string, so restrict the
                # cross-reference mapping to them; null- and array-valued variables are omitted
                # and their ${VAR} placeholders are left intact here.
                result[var_name] = Template(var_spec).safe_substitute(
                    **{name: value for name, value in result.items() if isinstance(value, str)}
                )
            elif var_spec is None:
                result[var_name] = None
            elif isinstance(var_spec, dict) and "env" in var_spec:
                extra = set(var_spec) - {"env"}
                if "seq" in extra:
                    raise ValueError(f"Variable {var_name!r}: entry has both 'env' and 'seq'")
                if extra:
                    logger.error("Variable %r: env entry has unexpected key(s): %s", var_name, ", ".join(sorted(extra)))
                result[var_name] = os.environ[var_spec["env"]]
            elif isinstance(var_spec, dict) and "seq" in var_spec:
                result[var_name] = self._resolve_seq(var_name, var_spec, result)

        # Second pass: Key Vault secrets, whose scope/key/base may reference first-pass values.
        for var_name, var_spec in content.items():
            if not isinstance(var_spec, dict) or "env" in var_spec or "seq" in var_spec:
                continue
            extra = set(var_spec) - {"scope", "key", "base"}
            if extra:
                logger.error("Variable %r: secret entry has unexpected key(s): %s", var_name, ", ".join(sorted(extra)))
            if ("scope" in var_spec) != ("key" in var_spec):
                raise ValueError(f"Variable {var_name!r}: secret entry needs both 'scope' and 'key'; one is missing")
            scope = var_spec.get("scope")
            key = var_spec.get("key")
            if scope and key:
                resolved = {name: value for name, value in result.items() if isinstance(value, str)}
                value = self.dbutils.secrets.get(
                    Template(scope).safe_substitute(**resolved),
                    Template(key).safe_substitute(**resolved),
                )
                base = var_spec.get("base")
                result[var_name] = base.format(value) if base else value
        return result

    @staticmethod
    def _resolve_seq(var_name: str, var_spec: dict, resolved: dict) -> str | list[str]:
        """Expand a ``seq`` variable spec into a delimited string or a list of strings.

        The ``seq`` value is a ``str.format`` template with a ``{i}`` counter field (its format
        spec carries any zero-padding, e.g. ``ABD{i:02d}`` -> ``ABD01``); it is first
        ``${VAR}``-substituted from ``resolved`` so it may reference earlier variables. The
        counter runs ``count`` values from ``start`` (default 1) in steps of ``step`` (default 1).

        ``as`` selects the output form. ``"string"`` (the default) joins the rendered items with
        ``sep`` (default ``", "``) into one string. ``"array"`` returns the items as a list, which
        ``read_json`` splices as sibling elements into the JSON array holding the ``"${VAR}"``
        placeholder -- ``["OTHER", "${VAR}"]`` -> ``["OTHER", "a", "b"]`` -- rather than nesting a
        sub-array (``sep`` is unused in this mode). ``count`` is required and must be positive,
        ``step`` must be non-negative; a missing or out-of-range value, or an ``as`` other than
        ``"string"`` or ``"array"``, raises ``ValueError``.
        """
        extra = set(var_spec) - {"seq", "start", "count", "step", "sep", "as"}
        if extra:
            logger.error("Variable %r: seq entry has unexpected key(s): %s", var_name, ", ".join(sorted(extra)))
        if "count" not in var_spec:
            raise ValueError(f"Variable {var_name!r}: seq entry requires a 'count'")
        as_kind = var_spec.get("as", "string")
        if as_kind not in ("string", "array"):
            raise ValueError(f"Variable {var_name!r}: seq 'as' must be 'string' or 'array', got {as_kind!r}")
        start = var_spec.get("start", 1)
        step = var_spec.get("step", 1)
        count = var_spec["count"]
        if count < 1:
            raise ValueError(f"Variable {var_name!r}: seq 'count' must be positive, got {count}")
        if step < 0:
            raise ValueError(f"Variable {var_name!r}: seq 'step' must be non-negative, got {step}")
        template = Template(var_spec["seq"]).safe_substitute(
            **{name: value for name, value in resolved.items() if isinstance(value, str)}
        )
        items = [template.format(i=start + step * n) for n in range(count)]
        if as_kind == "array":
            return items
        sep = var_spec.get("sep", ", ")
        return sep.join(items)

    def read_json(self, filepath: str, var_filepath: str = None) -> dict:
        """Read a JSON file, substituting ${VAR} placeholders from var_filepath when given.

        A variable whose value is not a string acts on a *complete* JSON string value
        (``"${VAR}"`` or ``"$VAR"``), never one embedded in a larger string. A ``None`` becomes a
        bare JSON ``null``. A ``seq`` array is spliced as comma-separated string elements into the
        JSON array that holds the placeholder (``["OTHER", "${VAR}"]`` -> ``["OTHER", "a", "b"]``).
        Such a variable embedded in a larger string cannot be substituted and is left intact with a
        warning, as is any placeholder the variables file does not supply.
        """
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        if var_filepath is not None:
            variables = self.read_variables(var_filepath)
            content = self._substitute(content, variables, filepath)
        return json.loads(content)

    @staticmethod
    def _substitute(content: str, variables: dict, filepath: str) -> str:
        """Substitute ${VAR} placeholders in JSON text from variables, returning the new text.

        Split out from read_json so callers with an already-resolved variables mapping (e.g.
        one merged from several files) can substitute it into a document without re-reading a
        variables file. See read_json for the null/array-substitution and unresolved-variable
        rules.
        """
        unresolved = unresolved_variables(content, variables)
        if unresolved:
            logger.warning("Unresolved variable(s) in %s: %s", filepath, ", ".join(unresolved))
        # Non-string values cannot be embedded in a larger string; each acts on a complete
        # "${VAR}" placeholder. A null becomes a bare JSON ``null``. A seq array (a list, always
        # non-empty since count must be positive) is spliced into its enclosing JSON array as
        # comma-separated string elements -- ["OTHER", "${VAR}"] -> ["OTHER", "a", "b"]. The
        # remaining string-valued variables are then textually substituted.
        str_vars = {name: value for name, value in variables.items() if isinstance(value, str)}
        null_vars = [name for name, value in variables.items() if value is None]
        array_vars = {name: value for name, value in variables.items() if isinstance(value, list)}
        for name in null_vars:
            content = content.replace(f'"${{{name}}}"', "null").replace(f'"${name}"', "null")
        for name, items in array_vars.items():
            elements = ", ".join(json.dumps(item) for item in items)
            content = content.replace(f'"${{{name}}}"', elements).replace(f'"${name}"', elements)
        embedded = sorted((set(null_vars) | set(array_vars)) & set(unresolved_variables(content, str_vars)))
        if embedded:
            logger.warning(
                "Non-string variable(s) embedded in a string in %s, left unresolved: %s",
                filepath,
                ", ".join(embedded),
            )
        return Template(content).safe_substitute(**str_vars)

    @staticmethod
    def _deep_merge(base, incoming, path: str):
        """Recursively merge ``incoming`` into ``base`` and return the merged value.

        Two dicts are merged key by key; two lists are concatenated (``incoming`` appended
        after ``base``); any other pair is treated as a leaf where the later value wins. A key
        whose two values differ in container-ness (a list opposite a dict or non-null scalar,
        or a dict opposite a non-null scalar) cannot be merged or appended and raises
        ``ValueError`` -- except that a JSON ``null`` opposite a container is allowed as a
        leaf override (null blanks out a container, or a container replaces null).

        List appends and value-preserving redefinitions (a leaf replaced by an equal value)
        are logged at INFO; a leaf override that discards a differing value -- including a
        null/container override -- is logged at WARNING. ``path`` is the dotted key path used
        only in those log messages.
        """
        if isinstance(base, dict) and isinstance(incoming, dict):
            for key, value in incoming.items():
                key_path = f"{path}.{key}" if path else key
                base[key] = VariableResolver._deep_merge(base[key], value, key_path) if key in base else value
            return base
        if isinstance(base, list) and isinstance(incoming, list):
            logger.info("Appended %d item(s) to '%s'", len(incoming), path)
            return base + incoming
        if isinstance(base, (dict, list)) or isinstance(incoming, (dict, list)):
            # A JSON null opposite a container is not a hard conflict: null blanks out a
            # container, or is itself replaced by one. Warn (a value is discarded) and let
            # the later file win, rather than raising as for two mismatched containers.
            if base is None or incoming is None:
                logger.warning("'%s' overridden: %r -> %r", path, base, incoming)
                return incoming
            raise ValueError(
                f"Conflicting types for '{path}': cannot merge " f"{type(base).__name__} with {type(incoming).__name__}"
            )
        if base == incoming:
            logger.info("'%s' redefined with an identical value", path)
        else:
            logger.warning("'%s' overridden: %r -> %r", path, base, incoming)
        return incoming

    def read_jsons(self, filepaths: list[str], var_filepaths: list[str] = None) -> dict:
        """Read and deep-merge several JSON config files, substituting ${VAR} placeholders.

        The variables files in ``var_filepaths`` are merged *before* resolution: their raw
        entries are combined into a single mapping (a later file's entry overrides an earlier
        one of the same name, with a warning) and then resolved in one two-pass run, so a
        variable in one file may reference one defined in an earlier file. The resolved
        variables are substituted into every config file in ``filepaths``.

        The config files are then deep-merged in order into a single object: nested objects
        merge key by key, lists concatenate (a later file's items are appended), and a scalar
        leaf takes the value from the last file that sets it. A key given incompatible shapes
        by two files (e.g. a list in one and an object in another) raises ``ValueError`` --
        though a JSON ``null`` opposite a list or object is allowed as a last-wins override,
        with a warning. A config file whose top level is not a JSON object also raises
        ``ValueError``. See _deep_merge for the per-conflict logging and read_json for the
        per-file substitution and null rules.
        """
        variables = {}
        if var_filepaths:
            merged_raw_vars = {}
            for var_filepath in var_filepaths:
                with open(var_filepath, encoding="utf-8") as f:
                    raw_vars = json.load(f)
                # Names already merged that this file redefines (dict key views support set ops).
                overridden = merged_raw_vars.keys() & raw_vars.keys()
                if overridden:
                    logger.warning(
                        "Variable(s) in %s override an earlier definition: %s",
                        var_filepath,
                        ", ".join(sorted(overridden)),
                    )
                merged_raw_vars.update(raw_vars)
            variables = self._resolve_variables(merged_raw_vars)

        merged_spec = {}
        for filepath in filepaths:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            if var_filepaths:
                content = self._substitute(content, variables, filepath)
            spec = json.loads(content)
            if not isinstance(spec, dict):
                raise ValueError(f"Config file {filepath} is not a JSON object (got {type(spec).__name__})")
            merged_spec = self._deep_merge(merged_spec, spec, "")
        return merged_spec


def configure_json(filepath: str, *, dbutils=None, var_filepath: str = None) -> dict:
    """Read a JSON config file and resolve its ${VAR} placeholders in one call.

    Convenience wrapper that instantiates a VariableResolver with ``dbutils`` and returns
    ``read_json(filepath, var_filepath=var_filepath)``. ``dbutils`` is only needed when the
    variables file contains Key Vault secrets. See VariableResolver.read_json for details.
    """
    return VariableResolver(dbutils=dbutils).read_json(filepath, var_filepath=var_filepath)


def configure_jsons(filepaths: list[str], *, dbutils=None, var_filepaths: list[str] = None) -> dict:
    """Read and deep-merge several JSON config files, resolving ${VAR} placeholders in one call.

    Convenience wrapper that instantiates a VariableResolver with ``dbutils`` and returns
    ``read_jsons(filepaths, var_filepaths=var_filepaths)``. ``dbutils`` is only needed when a
    variables entry is a Key Vault secret. See VariableResolver.read_jsons for details.
    """
    return VariableResolver(dbutils=dbutils).read_jsons(filepaths, var_filepaths=var_filepaths)
