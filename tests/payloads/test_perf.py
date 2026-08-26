from unittest.mock import Mock, patch

import pytest

from expb.payloads.executor.executor import Executor
from expb.payloads.executor.perf import fold_perf_script, summarize_folded

# Two samples sharing a prefix, one native leaf and one managed leaf, plus a sample
# with an unresolvable frame — the shape `perf script` emits for a containerized
# .NET process with a perf map.
PERF_SCRIPT = """\
nethermind 12345/12350 1234.567890:  10101010 cycles:
\tffffb1c2d300 __memmove_aarch64+0x40 (/usr/lib/aarch64-linux-gnu/libc.so.6)
\tffffb1c2d200 rocksdb::BlockBasedTable::Get+0x1a4 (/nethermind/librocksdb.so)
\tffffb1c2d100 Nethermind.Db.Rocks.DbOnTheRocks.Get+0x2c (/tmp/perf-1.map)
\tffffb1c2d000 Nethermind.Blockchain.BlockProcessor.Process+0x88 (/tmp/perf-1.map)

nethermind 12345/12350 1234.667890:  10101010 cycles:
\tffffb1c2d200 rocksdb::BlockBasedTable::Get+0x1a4 (/nethermind/librocksdb.so)
\tffffb1c2d100 Nethermind.Db.Rocks.DbOnTheRocks.Get+0x2c (/tmp/perf-1.map)
\tffffb1c2d000 Nethermind.Blockchain.BlockProcessor.Process+0x88 (/tmp/perf-1.map)

nethermind 12345/12351 1234.767890:  10101010 cycles:
\tffffb1c2d400 [unknown] ([unknown])
\tffffb1c2d000 Nethermind.Blockchain.BlockProcessor.Process+0x88 (/tmp/perf-1.map)

nethermind 12345/12350 1234.867890:  10101010 cycles:
\tffffb1c2d300 __memmove_aarch64+0x40 (/usr/lib/aarch64-linux-gnu/libc.so.6)
\tffffb1c2d200 rocksdb::BlockBasedTable::Get+0x1a4 (/nethermind/librocksdb.so)
\tffffb1c2d100 Nethermind.Db.Rocks.DbOnTheRocks.Get+0x2c (/tmp/perf-1.map)
\tffffb1c2d000 Nethermind.Blockchain.BlockProcessor.Process+0x88 (/tmp/perf-1.map)
"""


def test_finalize_perf_before_start_is_a_noop():
    executor = Executor(config=Mock(), logger=Mock())

    executor._finalize_perf(None)

    assert executor._perf_process is None
    assert executor._perf_dir is None
    assert executor._perf_host_pid is None


def test_finalize_perf_after_start_failure_is_a_noop(tmp_path):
    executor = Executor(config=Mock(outputs_dir=tmp_path), logger=Mock())

    with (
        patch.object(executor, "_client_host_pid", return_value=12345),
        patch(
            "expb.payloads.executor.executor.subprocess.Popen",
            side_effect=OSError("perf unavailable"),
        ),
        pytest.raises(OSError, match="perf unavailable"),
    ):
        executor._start_perf(Mock(), frequency=99)

    assert executor._perf_process is None
    assert executor._perf_dir == tmp_path / "perf"
    assert executor._perf_host_pid == 12345

    executor._finalize_perf(None)

    assert executor._perf_process is None
    assert executor._perf_dir is None
    assert executor._perf_host_pid is None


def test_fold_orders_frames_root_first_and_counts_duplicates():
    folded = fold_perf_script(PERF_SCRIPT)
    assert folded[0] == (
        "nethermind;Nethermind.Blockchain.BlockProcessor.Process;"
        "Nethermind.Db.Rocks.DbOnTheRocks.Get;rocksdb::BlockBasedTable::Get;"
        "__memmove_aarch64 2"
    )
    # Every sample is accounted for exactly once.
    assert sum(int(line.rsplit(" ", 1)[1]) for line in folded) == 4


def test_fold_keeps_native_and_managed_frames_in_one_stack():
    stack = fold_perf_script(PERF_SCRIPT)[0]
    assert "Nethermind.Db.Rocks.DbOnTheRocks.Get" in stack  # managed, from the perf map
    assert "rocksdb::BlockBasedTable::Get" in stack  # native, from the DSO
    assert "__memmove_aarch64" in stack  # libc


def test_unresolved_frames_are_labelled_not_dropped():
    folded = fold_perf_script(PERF_SCRIPT)
    assert any(line.endswith("[unknown] 1") for line in folded)


def test_comm_prefix_can_be_disabled():
    assert not fold_perf_script(PERF_SCRIPT, comm_prefix=False)[0].startswith("nethermind;")


def test_summary_reports_coverage():
    summary = summarize_folded(fold_perf_script(PERF_SCRIPT))
    assert summary["samples"] == 4
    assert summary["stacks"] == 3
    assert summary["unresolved_pct"] == 25.0  # one of four samples
