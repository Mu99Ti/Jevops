from jevops.prefilter import build_event, fingerprint, normalize, should_consider


def test_normalize_replaces_numbers():
    assert normalize("timeout after 3000ms") == normalize("timeout after 4500ms")


def test_normalize_replaces_uuids_and_hex():
    assert normalize("request 550e8400-e29b-41d4-a716-446655440000 failed code 0xdeadbeef") == normalize(
        "request 6ba7b810-9dad-11d1-80b4-00c04fd430c8 failed code 0xcafebabe"
    )


def test_fingerprint_is_stable_across_variable_values():
    a = fingerprint("api", "ERROR", "pool exhausted: 190/200")
    b = fingerprint("api", "ERROR", "pool exhausted: 195/200")
    assert a == b


def test_fingerprint_differs_by_service():
    assert fingerprint("api", "ERROR", "boom") != fingerprint("web", "ERROR", "boom")


def test_should_consider_respects_min_level():
    from jevops.models import LogEvent

    err = LogEvent(id="1", ts="", service="api", env="prod", level="ERROR", message="x")
    info = LogEvent(id="2", ts="", service="api", env="prod", level="INFO", message="x")
    assert should_consider(err, min_level="ERROR")
    assert not should_consider(info, min_level="ERROR")
    assert should_consider(info, min_level="INFO")


def test_should_consider_filters_known_noise():
    from jevops.models import LogEvent

    noise = LogEvent(id="1", ts="", service="api", env="prod", level="ERROR", message="health check failed ping")
    assert not should_consider(noise, min_level="ERROR", noise_patterns=("health check",))
    assert should_consider(noise, min_level="ERROR")


def test_build_event_sets_fingerprint_id_and_truncates():
    e = build_event({"service": "api", "level": "ERROR", "message": "x" * 5000}, max_message=100)
    assert len(e.message) == 100
    assert e.id == fingerprint("api", "ERROR", "x" * 5000)
