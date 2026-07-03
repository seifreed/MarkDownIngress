from __future__ import annotations

from _pytest.monkeypatch import MonkeyPatch

import markdown_ingress.core.config_env as config_env


def test_read_env_reads_present_and_missing_vars(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MDI_UNIT_TEST_VAR", "value")
    assert config_env.read_env("MDI_UNIT_TEST_VAR") == "value"
    monkeypatch.delenv("MDI_MISSING_VAR", raising=False)
    assert config_env.read_env("MDI_MISSING_VAR") is None


def test_read_bool_env_supports_truthy_and_falsey_values(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MDI_UNIT_TEST_BOOL", "YeS")
    assert config_env.read_bool_env("MDI_UNIT_TEST_BOOL") is True

    monkeypatch.setenv("MDI_UNIT_TEST_BOOL", "no")
    assert config_env.read_bool_env("MDI_UNIT_TEST_BOOL") is False


def test_read_bool_env_invalid_and_missing_fall_back_to_default(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDI_UNIT_TEST_BOOL", "maybe")
    assert config_env.read_bool_env("MDI_UNIT_TEST_BOOL") is False

    monkeypatch.delenv("MDI_UNIT_TEST_BOOL", raising=False)
    assert config_env.read_bool_env("MDI_UNIT_TEST_BOOL", default=True) is True


def test_read_positive_int_env_enforces_minimum(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDI_UNIT_TEST_INT", "3")
    assert config_env.read_positive_int_env("MDI_UNIT_TEST_INT", 7, minimum=2) == 3

    monkeypatch.setenv("MDI_UNIT_TEST_INT", "1")
    assert config_env.read_positive_int_env("MDI_UNIT_TEST_INT", 7, minimum=2) == 7


def test_read_float_env_and_optional_float_env(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDI_UNIT_TEST_FLOAT", "0.5")
    assert config_env.read_float_env("MDI_UNIT_TEST_FLOAT", 2.0) == 0.5

    monkeypatch.setenv("MDI_UNIT_TEST_FLOAT", "not-a-number")
    assert config_env.read_float_env("MDI_UNIT_TEST_FLOAT", 2.0) == 2.0

    monkeypatch.setenv("MDI_UNIT_TEST_OPTIONAL", "0.25")
    assert config_env.read_optional_float_env("MDI_UNIT_TEST_OPTIONAL", minimum=0.1) == 0.25

    monkeypatch.setenv("MDI_UNIT_TEST_OPTIONAL", "0")
    assert config_env.read_optional_float_env("MDI_UNIT_TEST_OPTIONAL", minimum=0.1) is None
