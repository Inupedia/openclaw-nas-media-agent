import io
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from output_contract import success
from resource_agent import CliUsageError, emit, main, parse_args


class DummyStore:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class DummyService:
    def __init__(self):
        self.calls = []

    def search(self, query, media_type=None, update=False):
        self.calls.append((query, media_type, update))
        return success(
            {
                "local": {"found": True, "title": "凡人修仙传"},
                "missing": [],
                "remoteCandidates": [],
            },
            terminal=True,
            next_action="stop_local_exists",
        )


class MediaCtlContractTests(unittest.TestCase):
    def test_executable_exists_at_fixed_repository_path(self):
        executable = ROOT / "bin" / "mediactl"
        text = executable.read_text(encoding="utf-8")

        self.assertTrue(executable.is_file())
        self.assertTrue(
            text.startswith("#!/bin/sh") or text.startswith("#!/usr/bin/env python3")
        )
        self.assertIn("resource_agent.py", text)

    def test_parser_supports_local_first_search(self):
        args = parse_args(
            [
                "search",
                "凡人修仙传",
                "--media-type",
                "anime",
                "--update",
            ]
        )

        self.assertEqual(args.command, "search")
        self.assertEqual(args.query, "凡人修仙传")
        self.assertEqual(args.media_type, "anime")
        self.assertTrue(args.update)

    def test_parser_supports_drama_media_type(self):
        args = parse_args(["search", "Example Show", "--media-type", "drama"])

        self.assertEqual(args.media_type, "drama")

    def test_parser_supports_validation_and_separate_organize_plan(self):
        validate = parse_args(["downloads", "validate", "rd-show"])
        organize_plan = parse_args(["organize", "plan", "rd-show"])
        organize_execute = parse_args(
            ["organize", "execute", "plan-example", "--confirmed"]
        )

        self.assertEqual(validate.download_command, "validate")
        self.assertEqual(validate.task_id, "rd-show")
        self.assertEqual(organize_plan.organize_command, "plan")
        self.assertEqual(organize_plan.task_id, "rd-show")
        self.assertEqual(organize_execute.organize_command, "execute")
        self.assertTrue(organize_execute.confirmed)

    def test_unknown_command_is_bounded_error(self):
        with self.assertRaisesRegex(CliUsageError, "invalid command"):
            parse_args(["shell", "curl", "http://example"])

    def test_secret_arguments_are_not_echoed(self):
        with self.assertRaises(CliUsageError) as raised:
            parse_args(["search", "x", "--token", "danger"])

        self.assertNotIn("danger", str(raised.exception))

    def test_emit_prints_exactly_one_json_document(self):
        stream = io.StringIO()

        emit(
            success(
                {"local": {"found": True}},
                terminal=True,
                next_action="stop_local_exists",
            ),
            stream=stream,
        )

        result = json.loads(stream.getvalue())
        self.assertEqual(result["nextAction"], "stop_local_exists")
        self.assertEqual(stream.getvalue().count("\n"), 1)

    def test_main_routes_search_through_media_service(self):
        store = DummyStore()
        service = DummyService()
        stream = io.StringIO()

        exit_code = main(
            ["search", "凡人修仙传", "--media-type", "anime"],
            runtime_loader=lambda: (
                {},
                store,
                object(),
                object(),
                object(),
                object(),
                None,
            ),
            service_factory=lambda routing, loaded_store, qas, pansou, jiaofu=None: service,
            stream=stream,
        )

        result = json.loads(stream.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["nextAction"], "stop_local_exists")
        self.assertEqual(service.calls, [("凡人修仙传", "anime", False)])
        self.assertTrue(store.closed)


if __name__ == "__main__":
    unittest.main()
