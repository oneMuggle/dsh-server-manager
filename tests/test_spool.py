from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gateway.services.spool import SpoolError, receive_spool


async def chunks(*values: bytes):
    for value in values:
        yield value


class SpoolServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_writes_complete_stream_and_reports_size(self) -> None:
        result = await receive_spool(
            chunks(b"abc", b"def"),
            self.root,
            suffix=".upload",
            max_bytes=10,
            content_length="6",
            allow_empty=True,
        )

        self.assertEqual(result.size, 6)
        self.assertEqual(result.path.read_bytes(), b"abcdef")
        self.assertRegex(result.name, r"^[0-9a-f]{32}\.upload$")

    async def test_rejects_invalid_declared_lengths_before_creating_file(self) -> None:
        for value, code in (
            ("invalid", "invalid_length"),
            ("-1", "invalid_length"),
            ("11", "declared_too_large"),
        ):
            with self.subTest(value=value), self.assertRaises(SpoolError) as caught:
                await receive_spool(
                    chunks(b"abc"),
                    self.root,
                    suffix=".upload",
                    max_bytes=10,
                    content_length=value,
                    allow_empty=True,
                )
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(list(self.root.iterdir()), [])

    async def test_removes_partial_file_when_stream_exceeds_limit(self) -> None:
        with self.assertRaises(SpoolError) as caught:
            await receive_spool(
                chunks(b"123456", b"78901"),
                self.root,
                suffix=".upload",
                max_bytes=10,
                content_length=None,
                allow_empty=True,
            )
        self.assertEqual(caught.exception.code, "too_large")
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_regular_upload_permits_empty_file(self) -> None:
        result = await receive_spool(
            chunks(),
            self.root,
            suffix=".upload",
            max_bytes=10,
            content_length="0",
            allow_empty=True,
        )
        self.assertEqual(result.size, 0)
        self.assertEqual(result.path.read_bytes(), b"")

    async def test_plugin_upload_rejects_declared_and_streamed_empty_content(self) -> None:
        with self.assertRaises(SpoolError) as declared:
            await receive_spool(
                chunks(),
                self.root,
                suffix=".plugin.tgz",
                max_bytes=10,
                content_length="0",
                allow_empty=False,
            )
        self.assertEqual(declared.exception.code, "declared_empty")

        with self.assertRaises(SpoolError) as streamed:
            await receive_spool(
                chunks(),
                self.root,
                suffix=".plugin.tgz",
                max_bytes=10,
                content_length=None,
                allow_empty=False,
            )
        self.assertEqual(streamed.exception.code, "empty")
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_plugin_spool_uses_expected_name(self) -> None:
        result = await receive_spool(
            chunks(b"package"),
            self.root,
            suffix=".plugin.tgz",
            max_bytes=10,
            content_length="7",
            allow_empty=False,
        )
        self.assertRegex(result.name, r"^[0-9a-f]{32}\.plugin\.tgz$")


if __name__ == "__main__":
    unittest.main()
