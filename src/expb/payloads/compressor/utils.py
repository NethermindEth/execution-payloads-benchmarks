import math

# 1024-based unit multipliers, matching how Docker parses memory limits
# (docker-py's ``docker.utils.parse_bytes``). Keeping the same semantics means a
# limit Docker accepts for the container is also accepted here when the value is
# converted for the Nethermind memory hint.
_UNIT_MULTIPLIERS = {
    "b": 1,
    "k": 1024,
    "m": 1024**2,
    "g": 1024**3,
}


def convert_mem_limit_to_bytes(mem_limit: str) -> int:
    """Convert a Docker-style memory-limit string to a number of bytes.

    Accepts the same forms Docker does, case-insensitively: a number with an
    optional ``b``/``k``/``m``/``g`` unit (also written as the two-letter
    ``kb``/``mb``/``gb``), or a bare byte count. Units are 1024-based. Examples:
    ``"32g"``, ``"512M"``, ``"1gb"``, ``"1024k"``, ``"1048576"``.

    The previous implementation only handled lowercase single-letter units and
    used ``str.replace``, so valid Docker values such as ``"8G"``, ``"1gb"`` or a
    bare byte count raised ``ValueError`` even though ``compressor.py`` passes the
    very same string straight to Docker for the container's ``mem_limit``.

    Raises:
        ValueError: if ``mem_limit`` is empty, negative, not a finite number, or
            uses an unrecognised unit.
    """
    raw = mem_limit.strip().lower()
    if not raw:
        raise ValueError("Memory limit is empty")

    # Normalise a two-letter unit (kb/mb/gb) to its single-letter form; a lone
    # trailing "b" is the bytes unit and is handled below.
    if len(raw) >= 2 and raw.endswith("b") and raw[-2] in _UNIT_MULTIPLIERS:
        raw = raw[:-1]

    if raw[-1] in _UNIT_MULTIPLIERS:
        unit, number = raw[-1], raw[:-1]
    else:
        unit, number = "b", raw

    try:
        value = float(number)
    except ValueError:
        raise ValueError(f"Invalid memory limit: {mem_limit!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"Memory limit must be a finite number: {mem_limit!r}")
    if value < 0:
        raise ValueError(f"Memory limit cannot be negative: {mem_limit!r}")

    return int(value * _UNIT_MULTIPLIERS[unit])
