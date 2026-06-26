# brickvar — project context

`brickvar` is a small, standalone, public Python package that resolves configuration
variables for Databricks jobs (literals, environment variables, and Azure Key Vault
secrets via `dbutils.secrets`) and substitutes them into JSON config files. It has
**no runtime dependencies** — `dbutils` is injected by the caller.

> Internal/process notes and references to private repos live in `CLAUDE.local.md`
> (gitignored), not here.

## Public API

- **`VariableResolver`** — `read_variables(filepath)` and
  `read_json(filepath, var_filepath=None)`. (Renamed from `ConfigManager`, since 0.0.4 —
  no back-compat alias.)
- Module-level helper **`unresolved_variables(content, provided)`**.
- Package-level convenience **`configure_json(filepath, *, dbutils=None, var_filepath=None)`**
  (since 0.0.2) — instantiates a `VariableResolver` and calls `read_json` in one step.

Prefer `configure_json` for the common case; use `VariableResolver` directly when you need
`read_variables` or want to reuse an instance.

### Variables-file entry forms

Each entry maps a name to one of:

- a **literal** string (may reference earlier variables with `${VAR}`),
- **`null`** (since 0.0.3) — substitutes a real JSON `null`. The placeholder must be a
  complete string value `"${VAR}"`; an embedded `"prefix-${VAR}"` can't become null and is
  left intact with a warning,
- an **environment variable** — `{"env": "NAME"}`,
- a **Databricks / Azure Key Vault secret** — `{"scope": ..., "key": ..., "base"?: ...}`,
  read via `dbutils.secrets`.

Resolution is two-pass, so a secret's `scope`/`key` can reference already-resolved literal
or environment values. Unknown `${VAR}` placeholders are left intact.

## Repo & layout

- `https://github.com/amida-tech/brickvar` (public, Apache-2.0).
- src layout: `src/brickvar/`, `pyproject.toml`, `tests/`.
- Default branch **`develop`**; releases flow `develop` → `main`. Both branches are
  protected (1 approval required).

## Current status

- `pytest`: **22 passing**. `python -m build` + `twine check dist/*`: passing.
- Latest on PyPI: **0.0.3** (https://pypi.org/project/brickvar/).

## Dev setup

```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Releasing

Publishing to PyPI uses **Trusted Publishing (OIDC)** — no stored token. A published
GitHub Release triggers `.github/workflows/release.yml` (build → `twine check` → publish
via `pypa/gh-action-pypi-publish`). The `pypi` environment has a required-reviewer gate, so
the publish job pauses until approved.

To cut a release:

1. Bump the version in **one place**: `__version__` in `src/brickvar/__init__.py`.
   `pyproject.toml` reads it dynamically (`[tool.setuptools.dynamic] version = {attr = ...}`).
   PyPI rejects re-uploading an existing version, so always bump first.
2. Land the bump on `main` (via `develop` → `main`).
3. Publish a **GitHub Release** tagged `vX.Y.Z` targeting `main` (drafting first lets you
   review notes; drafts do not trigger the workflow).
4. Approve the `pypi` deployment when the run pauses; confirm the version on PyPI.

## Open questions

- _(none open)_ — the `ConfigManager` → `VariableResolver` rename and the single-source
  version (dynamic in `pyproject.toml`) are both done on `feature/multi-file-and-api-cleanup`.
