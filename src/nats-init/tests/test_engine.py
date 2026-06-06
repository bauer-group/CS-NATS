"""
Pure-function unit tests for the NATS init engine.

These exercise the env-resolution and CLI-argument helpers without a running
cluster or the `nats` binary present — so `pytest` is green in CI.
"""

import json

import pytest

from nats_cli import (
    NatsCli,
    Result,
    resolve_config_values,
    resolve_env_vars,
    write_temp_json,
)


# --- env resolution ----------------------------------------------------------

def test_resolve_env_vars_substitutes(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    assert resolve_env_vars("x-${FOO}-y") == "x-bar-y"


def test_resolve_env_vars_missing_raises(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(ValueError):
        resolve_env_vars("${NOPE}")


def test_resolve_config_values_recurses(monkeypatch):
    monkeypatch.setenv("STREAM", "app-events")
    cfg = {"streams": [{"name": "${STREAM}", "num_replicas": 3}]}
    out = resolve_config_values(cfg)
    assert out["streams"][0]["name"] == "app-events"
    assert out["streams"][0]["num_replicas"] == 3


def test_resolve_config_values_comment_passthrough(monkeypatch):
    # _-prefixed keys are comments: their literal ${...} must NOT be resolved.
    cfg = {"_comment": "uses ${UNSET_VAR} as an example", "kv": []}
    out = resolve_config_values(cfg)
    assert out["_comment"] == "uses ${UNSET_VAR} as an example"


# --- NatsCli argument building -----------------------------------------------

def test_natscli_base_full():
    cli = NatsCli("nats://nats-1:4222", creds="/creds/admin.creds", tls_ca="/certs/ca.pem")
    assert cli.base == [
        "nats", "--server", "nats://nats-1:4222",
        "--creds", "/creds/admin.creds",
        "--tlsca", "/certs/ca.pem",
    ]


def test_natscli_base_minimal():
    cli = NatsCli("nats://nats-1:4222")
    assert cli.base == ["nats", "--server", "nats://nats-1:4222"]


def test_result_ok_and_error():
    assert Result(0, "out", "").ok is True
    r = Result(1, "", "boom")
    assert r.ok is False
    assert "boom" in r.error


# --- temp json roundtrip -----------------------------------------------------

def test_write_temp_json_roundtrip():
    import os

    path = write_temp_json({"name": "s1", "num_replicas": 3})
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data == {"name": "s1", "num_replicas": 3}
    finally:
        os.unlink(path)
