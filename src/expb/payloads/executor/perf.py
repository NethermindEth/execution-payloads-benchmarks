"""Helpers for the host-side `perf` sidecar.

`perf script` output is verbose and per-sample; folded stacks are one line per
unique stack with a sample count, which is what the reporting tooling consumes and
what stays readable when a profile covers millions of samples.
"""

import re

# "	ffffb1c2d3e4 Namespace.Type.Method+0x2c (/tmp/perf-1.map)" — perf indents frame
# lines, leaf first. The address and the originating DSO are noise for folding.
_FRAME = re.compile(r"^\s+\S+\s+(?P<symbol>.*?)(?:\+0x[0-9a-fA-F]+)?\s+\((?P<dso>[^)]*)\)\s*$")

# Frames perf could not attribute at all. Keeping them collapses unrelated stacks
# together, so they are labelled with their DSO where one is known.
_UNKNOWN = "[unknown]"


def _frame_label(symbol: str, dso: str) -> str:
    if symbol and symbol != _UNKNOWN:
        return symbol
    if not dso or dso in ("[unknown]", ""):
        return _UNKNOWN
    return f"{_UNKNOWN} ({dso.rsplit('/', 1)[-1]})"


def fold_perf_script(text: str, comm_prefix: bool = True) -> list[str]:
    """Fold `perf script` output into "frame;frame;... count" lines.

    Args:
        text: raw `perf script` output.
        comm_prefix: prefix each stack with the sampled command, matching the
            convention of stackcollapse-perf so flame graphs group by process.

    Returns:
        Folded lines sorted by descending sample count, then by stack for stability.
    """
    counts: dict[str, int] = {}
    comm = ""
    frames: list[str] = []

    def flush() -> None:
        nonlocal frames
        if frames:
            stack = list(reversed(frames))
            if comm_prefix and comm:
                stack.insert(0, comm)
            key = ";".join(stack)
            counts[key] = counts.get(key, 0) + 1
        frames = []

    for line in text.splitlines():
        if not line.strip():
            flush()
            continue
        match = _FRAME.match(line)
        if match is not None:
            frames.append(_frame_label(match.group("symbol").strip(), match.group("dso")))
            continue
        if line[0].isspace():
            # An indented line perf formatted differently; do not silently drop it.
            frames.append(_UNKNOWN)
            continue
        # Sample header: "comm  pid/tid  timestamp:  period  event:"
        flush()
        comm = line.split()[0] if line.split() else ""

    flush()
    return [
        f"{stack} {count}"
        for stack, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def summarize_folded(folded: list[str]) -> dict[str, object]:
    """Sample totals and symbolization coverage, for logging after a capture.

    A profile that recorded fine but symbolized badly looks identical to a good one
    until someone opens it, so the unresolved share is worth reporting up front.
    """
    total = 0
    unresolved = 0
    leaves: dict[str, int] = {}
    for line in folded:
        stack, _, count_text = line.rpartition(" ")
        try:
            count = int(count_text)
        except ValueError:
            continue
        total += count
        leaf = stack.rsplit(";", 1)[-1]
        leaves[leaf] = leaves.get(leaf, 0) + count
        if leaf.startswith(_UNKNOWN):
            unresolved += count
    top = sorted(leaves.items(), key=lambda kv: -kv[1])[:5]
    return {
        "stacks": len(folded),
        "samples": total,
        "unresolved_pct": round(unresolved * 100 / total, 2) if total else 0.0,
        "top_leaves": [f"{name}={count}" for name, count in top],
    }
