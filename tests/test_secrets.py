from invinv.core.secrets import PREFIX, decrypt, encrypt


def test_round_trip_and_wrong_key() -> None:
    stored = encrypt("123:token", "secret one")

    assert stored.startswith(PREFIX) and "123:token" not in stored
    assert decrypt(stored, "secret one") == "123:token"
    assert decrypt(stored, "secret two") is None


def test_a_value_saved_before_encryption_reads_as_is() -> None:
    assert decrypt("123:token", "any") == "123:token"
