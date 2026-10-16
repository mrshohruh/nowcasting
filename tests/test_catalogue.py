from nowcast.catalogue import load, validate


def test_catalogue_is_valid():
    problems = validate(load())
    assert problems == [], "\n".join(problems)


def test_every_block_has_a_core_series():
    series = load()
    blocks = {s["block"] for s in series if s["status"] == "core"}
    assert {"real", "services", "external", "prices_finance", "firms", "quarterly", "target"} <= blocks


def test_validator_catches_missing_field():
    bad = [{"id": "x", "name": "x"}]
    assert any("missing publisher" in p for p in validate(bad))
