"""Tests for config/commands.json loading and CLI enforcement."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from command_contract import (  # noqa: E402
    ContractError,
    command_key,
    enforce_invocation,
    leaf_command_keys_from_parser,
    load_command_contract,
    recovery_preconditions,
    required_services,
)
from resource_agent import main, parse_args  # noqa: E402


class CommandContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = load_command_contract()

    def test_contract_version_and_required_keys(self):
        self.assertEqual(self.contract["version"], "0.4.2")
        commands = self.contract["commands"]
        for key in (
            "library.lookup",
            "execute",
            "downloads.list",
            "downloads.show",
            "downloads.recover.plan",
            "downloads.recover.execute",
            "organize.execute",
            "check-ready",
            "downloads.validate",
        ):
            self.assertIn(key, commands)
        self.assertEqual(commands["execute"]["confirmation"], "required")
        self.assertEqual(commands["downloads.validate"]["requires_services"], [])
        self.assertEqual(required_services("library.lookup", self.contract), set())
        self.assertEqual(
            required_services("execute", self.contract),
            {"qas", "aria2"},
        )
        pre = recovery_preconditions(self.contract)
        self.assertEqual(pre["taskStates"], {"error", "partial_failed"})
        self.assertEqual(pre["errorCodesExact"], {"16"})

    def test_cli_leaf_commands_match_contract(self):
        keys = leaf_command_keys_from_parser(parse_args)
        self.assertEqual(keys, set(self.contract["commands"]))

    def test_command_key_mapping(self):
        self.assertEqual(
            command_key(parse_args(["library", "lookup", "牧神记"])),
            "library.lookup",
        )
        self.assertEqual(
            command_key(parse_args(["downloads", "recover", "plan", "rd-1"])),
            "downloads.recover.plan",
        )

    def test_enforce_confirmation_and_recovery_env(self):
        with self.assertRaises(ContractError) as ctx:
            enforce_invocation(parse_args(["execute", "plan-1"]), contract=self.contract)
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")
        with patch.dict(os.environ, {"QUARK_RECOVERY_ENABLED": "false"}, clear=False):
            with self.assertRaises(ContractError) as ctx:
                enforce_invocation(
                    parse_args(
                        ["downloads", "recover", "execute", "plan-1", "--confirmed"]
                    ),
                    contract=self.contract,
                )
            self.assertEqual(ctx.exception.code, "RECOVERY_DISABLED")

    def test_main_rejects_unconfirmed_execute_via_contract(self):
        stream = io.StringIO()

        def fake_loader(command=None, contract_key=None):
            raise AssertionError("runtime must not load before confirmation fails")

        code = main(["execute", "plan-1"], runtime_loader=fake_loader, stream=stream)
        self.assertEqual(code, 1)
        payload = stream.getvalue()
        self.assertIn("requires --confirmed", payload)
        self.assertIn("CONFIRMATION_REQUIRED", payload)

    def test_library_runtime_does_not_require_qas(self):
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            routing = {
                "anime": {
                    "staging_root": str(Path(tmp) / "stage"),
                    "final_root": str(Path(tmp) / "final"),
                }
            }

            def fake_loader(command=None, contract_key=None):
                self.assertEqual(contract_key, "library.lookup")
                from state_store import StateStore

                return routing, StateStore(db), None, None, None, None, None

            class FakeCatalog:
                def lookup(self, query, media_type=None):
                    return [{"title": query, "path": "/volume2/影视/x"}]

            class FakeService:
                def __init__(self, *args, **kwargs):
                    self.catalog = FakeCatalog()

            with patch.dict(
                os.environ,
                {
                    "RESOURCE_AGENT_STATE_DB": str(db),
                    "QAS_BASE_URL": "",
                    "QAS_TOKEN": "",
                },
                clear=False,
            ):
                code = main(
                    ["library", "lookup", "牧神记"],
                    runtime_loader=fake_loader,
                    service_factory=lambda *a, **k: FakeService(),
                    stream=stream,
                )
        self.assertEqual(code, 0)
        self.assertIn("local_lookup_complete", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
