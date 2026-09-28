import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_typed_findings_and_device_effects_are_exposed(self):
        async with Client(server.mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        schema = tools['unify_evidence'].input_schema
        self.assertIn('evidence_dir', schema['required'])
        finding = schema['properties']['findings']['items']
        if '$ref' in finding:
            finding = schema['$defs'][finding['$ref'].split('/')[-1]]
        self.assertEqual(set(finding['required']), {'claim', 'analyzer', 'path'})
        self.assertTrue(tools['explore_apk'].annotations.destructive_hint)
        self.assertTrue(tools['inspect_exploration'].annotations.read_only_hint)

    async def test_analyzer_does_not_block_mcp_event_loop(self):
        def analyze(*args):
            time.sleep(0.3)
            return {'output_dir': 'fixture'}

        async with Client(server.mcp) as client:
            async def timer():
                start = time.monotonic()
                await asyncio.sleep(0.03)
                return time.monotonic() - start

            with patch.object(server.core, 'analyze_apk', side_effect=analyze):
                _, elapsed = await asyncio.gather(
                    client.call_tool('analyze_apk', {'apk_path': 'fixture', 'output_dir': 'fixture'}),
                    timer(),
                )
        self.assertLess(elapsed, 0.2)


if __name__ == '__main__':
    unittest.main()
