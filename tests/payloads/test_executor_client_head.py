import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from expb.payloads.executor.executor import Executor


class ClientHeadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.payloads_file = Path(self.directory.name) / "payloads.jsonl"
        self.payloads_file.write_text(
            "".join(
                json.dumps({"params": [{"blockNumber": hex(number)}]}) + "\n"
                for number in (100, 101, 102)
            ),
            encoding="utf-8",
        )
        self.executor = Executor(
            config=Mock(
                payloads_file=self.payloads_file,
                k6_payloads_skip=None,
                startup_block=None,
                startup_wait=0,
                json_rpc_wait_max_retries=6,
            ),
            logger=Mock(),
        )

    def test_first_payload_parent_respects_skip_and_end_of_file(self):
        for skip, expected in ((None, 99), (0, 99), (2, 101), (3, None)):
            with self.subTest(skip=skip):
                self.executor.config.k6_payloads_skip = skip
                self.assertEqual(expected, self.executor.first_payload_parent_number())

    def test_first_payload_parent_handles_genesis(self):
        self.payloads_file.write_text('{"params":[{"blockNumber":"0x0"}]}\n')
        self.assertEqual(0, self.executor.first_payload_parent_number())

    def test_first_payload_parent_does_not_parse_skipped_rows(self):
        self.payloads_file.write_text('not-json\n{"params":[{"blockNumber":"0x64"}]}\n')
        self.executor.config.k6_payloads_skip = 1
        self.assertEqual(99, self.executor.first_payload_parent_number())

    def test_first_payload_parent_rejects_invalid_selected_block_numbers(self):
        for number in (None, True, "100", "-0x1", "0x", "invalid"):
            with self.subTest(number=number):
                self.payloads_file.write_text(json.dumps({"params": [{"blockNumber": number}]}))
                with self.assertRaises(ValueError):
                    self.executor.first_payload_parent_number()

    def test_automatic_readiness_waits_until_the_head_reaches_parent_and_settles(self):
        self.assert_polling([50, 98, 99, 100, 100], expected_calls=5)

    def test_automatic_readiness_confirms_an_already_available_head(self):
        self.assert_polling([100, 100], expected_calls=2)

    def test_explicit_checkpoint_waits_through_a_pause_below_checkpoint(self):
        self.executor.config.startup_block = 102
        self.assert_polling([99, 99, 100, 102], expected_calls=4)

    def test_explicit_checkpoint_returns_without_a_confirmation_delay(self):
        self.executor.config.startup_block = 102
        self.assert_polling([102], expected_calls=1)

    def test_explicit_checkpoint_does_not_bypass_first_payload_parent(self):
        self.executor.config.startup_block = 50
        self.assert_polling([50, 99], expected_calls=2)

    def test_automatic_readiness_resets_confirmation_after_an_rpc_failure(self):
        self.assert_polling([100, None, 100, 100], expected_calls=4)

    def test_no_selected_payload_preserves_rpc_availability_check(self):
        self.executor.config.k6_payloads_skip = 3
        self.assert_polling([0], expected_calls=1)

    def test_explicit_checkpoint_applies_when_no_payload_is_selected(self):
        self.executor.config.k6_payloads_skip = 3
        self.executor.config.startup_block = 102
        self.assert_polling([101, 102], expected_calls=2)

    def test_automatic_readiness_uses_one_attempt_budget(self):
        self.executor.config.json_rpc_wait_max_retries = 3
        with self.assertRaisesRegex(Exception, "not ready after 3 attempts"):
            self.assert_polling([50, 99, 100], expected_calls=3)

    def test_automatic_readiness_rejects_a_stable_head_below_the_parent(self):
        self.executor.config.json_rpc_wait_max_retries = 3
        with self.assertRaisesRegex(Exception, "not ready after 3 attempts"):
            self.assert_polling([98, 98, 98], expected_calls=3)

    def assert_polling(self, heads, expected_calls):
        responses = []
        for head in heads:
            response = Mock(ok=True)
            response.json.return_value = (
                {"result": hex(head)}
                if head is not None
                else {"error": {"code": -32000}}
            )
            responses.append(response)
        with (
            patch("expb.payloads.executor.executor.requests.post", side_effect=responses) as post,
            patch("expb.payloads.executor.executor.time.sleep") as sleep,
        ):
            try:
                self.executor.wait_for_client_json_rpc("http://client:8545")
            finally:
                self.assertEqual(expected_calls, post.call_count)
                self.assertEqual(expected_calls - 1, sleep.call_count)
