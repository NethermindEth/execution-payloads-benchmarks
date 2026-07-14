import pytest

from expb.payloads.compressor.utils import convert_mem_limit_to_bytes

KIB = 1024
MIB = 1024**2
GIB = 1024**3


@pytest.mark.parametrize(
    "value, expected",
    [
        # lowercase single-letter units (already worked)
        ("32g", 32 * GIB),  # the CLI default
        ("8g", 8 * GIB),
        ("512m", 512 * MIB),
        ("1024k", 1024 * KIB),
        ("1b", 1),
        # uppercase units (previously raised ValueError)
        ("8G", 8 * GIB),
        ("512M", 512 * MIB),
        ("1G", 1 * GIB),
        # two-letter units (previously raised ValueError)
        ("1gb", 1 * GIB),
        ("1GB", 1 * GIB),
        ("512mb", 512 * MIB),
        ("1024kb", 1024 * KIB),
        # bare byte counts (previously raised ValueError)
        ("1048576", 1048576),
        ("0", 0),
        # surrounding whitespace (previously raised ValueError)
        (" 8g ", 8 * GIB),
        # fractional values, as Docker accepts
        ("1.5g", int(1.5 * GIB)),
    ],
)
def test_valid_mem_limits(value: str, expected: int) -> None:
    assert convert_mem_limit_to_bytes(value) == expected


@pytest.mark.parametrize(
    "value", ["", "   ", "abc", "-1g", "g", "1t", "1kib", "12x", "inf", "nan", "1e400"]
)
def test_invalid_mem_limits_raise(value: str) -> None:
    with pytest.raises(ValueError):
        convert_mem_limit_to_bytes(value)


def test_matches_docker_parse_bytes_for_configurable_values() -> None:
    # compressor.py passes the same string to docker-py for the container's
    # mem_limit, so the two parsers must agree on every value a user would
    # realistically configure.
    parse_bytes = pytest.importorskip("docker.utils").parse_bytes
    for value in ["32g", "8G", "512M", "1g", "1gb", "1GB", "1024k", "2G", "1048576"]:
        assert convert_mem_limit_to_bytes(value) == parse_bytes(value)
