from pathlib import Path

from jevops.config import Config, load_services_toml


def test_from_env_defaults(monkeypatch):
    for key in (
        "TYPESAFE_API_KEY",
        "TYPESAFE_MODEL",
        "TYPESAFE_BASE_URL",
        "JEVOPS_PORT",
        "JEVOPS_PAGE_MIN_CONFIDENCE",
        "JEVOPS_PAGE_MIN_HUMAN",
        "JEVOPS_COOLDOWN_SECONDS",
        "JEVOPS_MIN_LEVEL",
        "JEVOPS_TOP_K",
        "ES_INDEX_DECISIONS",
    ):
        monkeypatch.delenv(key, raising=False)
    c = Config.from_env()
    assert c.typesafe_model == "jev-latest"
    assert c.typesafe_base_url == "https://api.typesafe.ai"
    assert c.port == 8080
    assert c.top_k == 3
    assert c.min_level == "ERROR"
    assert c.cooldown_seconds == 1800
    assert c.es_index_decisions == "jevops-decisions"


def test_from_env_reads_jev_and_thresholds(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    monkeypatch.setenv("TYPESAFE_MODEL", "jev-1.13.0")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "http://localhost:20128/api")
    monkeypatch.setenv("JEVOPS_PAGE_MIN_CONFIDENCE", "0.9")
    monkeypatch.setenv("JEVOPS_PAGE_MIN_HUMAN", "0.8")
    monkeypatch.setenv("JEVOPS_COOLDOWN_SECONDS", "60")
    monkeypatch.setenv("JEVOPS_MIN_LEVEL", "WARN")
    c = Config.from_env()
    assert c.typesafe_api_key == "sk-test"
    assert c.typesafe_model == "jev-1.13.0"
    assert c.typesafe_base_url == "http://localhost:20128/api"
    assert c.page_min_confidence == 0.9
    assert c.page_min_human == 0.8
    assert c.cooldown_seconds == 60
    assert c.min_level == "WARN"


def test_load_services_toml(tmp_path: Path):
    p = tmp_path / "services.toml"
    p.write_text(
        '[service."checkout-api"]\nmin_confidence = 0.95\npage_enabled = false\n\n[service.web]\nmin_human = 0.95\n'
    )
    services = load_services_toml(p)
    assert services["checkout-api"].min_confidence == 0.95
    assert services["checkout-api"].page_enabled is False
    assert services["web"].min_human == 0.95


def test_load_services_toml_missing_file_returns_empty(tmp_path: Path):
    assert load_services_toml(tmp_path / "nope.toml") == {}
