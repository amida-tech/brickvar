"""Test fixtures for brickvar."""

import json

import pytest


class _MockSecrets:  # pylint: disable=too-few-public-methods
    """Mock of dbutils.secrets: get(scope, key) returns a deterministic f"{scope}_{key}_value"."""

    def __init__(self, mocker):
        self.get = mocker.Mock(side_effect=lambda scope, key: f"{scope}_{key}_value")


@pytest.fixture
def mock_dbutils(mocker):
    """A minimal dbutils whose secrets.get returns f"{scope}_{key}_value" and records calls."""
    dbutils = mocker.Mock()
    dbutils.secrets = _MockSecrets(mocker)
    return dbutils


@pytest.fixture
def write_json(tmp_path):
    """Return a helper that writes an object as JSON to tmp_path and returns the file path."""

    def _write(name, obj):
        path = tmp_path / name
        path.write_text(json.dumps(obj), encoding="utf-8")
        return str(path)

    return _write
