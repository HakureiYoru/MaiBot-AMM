"""验证旧插件 MCP 能力通过 1.3.5 的共享服务调用，不启动真实 Bot。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock

import ast
import unittest


class MCPSharedServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[2] / "src/plugin_runtime/capabilities/mcp.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        mixin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        self.configuration = object()
        self.service = SimpleNamespace(
            ensure_initialized=AsyncMock(),
            call_tool_invocation=AsyncMock(),
            list_tools=AsyncMock(return_value=[]),
        )
        namespace = {
            "Any": Any,
            "Dict": Dict,
            "MCPService": object,
            "ToolContentItem": object,
            "ToolInvocation": SimpleNamespace,
            "logger": Mock(),
            "get_mcp_service": lambda: self.service,
            "config_manager": SimpleNamespace(
                get_global_config=lambda: SimpleNamespace(mcp=self.configuration)
            ),
        }
        exec(compile(ast.Module(body=[mixin], type_ignores=[]), str(source), "exec"), namespace)
        self.runtime = namespace["RuntimeMCPCapabilityMixin"]()

    async def test_call_reuses_host_service_and_preserves_result(self):
        self.service.call_tool_invocation.return_value = SimpleNamespace(
            success=True, content="ok", error_message=None,
            structured_content={"value": 3}, content_items=[], metadata={"source": "mcp"},
        )
        result = await self.runtime._cap_mcp_call(
            "test", "mcp.call", {"tool_name": "test_tool", "arguments": {"value": 2}}
        )
        self.service.ensure_initialized.assert_awaited_once_with(self.configuration)
        invocation = self.service.call_tool_invocation.call_args.args[0]
        self.assertEqual(invocation.tool_name, "test_tool")
        self.assertEqual(invocation.arguments, {"value": 2})
        self.assertEqual(result["structured_content"], {"value": 3})
        self.assertTrue(result["success"])

    async def test_list_tools_reads_updated_shared_service(self):
        self.service.list_tools.return_value = [SimpleNamespace(
            name="test_tool", description="test", parameters_schema=None, provider_name="mcp",
        )]
        result = await self.runtime._cap_mcp_list_tools("test", "mcp.list_tools", {})
        self.service.list_tools.assert_awaited_once()
        self.assertEqual(result["tools"][0]["name"], "test_tool")
        self.assertEqual(result["tools"][0]["parameters"], {"type": "object", "properties": {}})

    async def test_invalid_call_does_not_initialize_connections(self):
        for arguments in ({}, {"tool_name": "tool", "arguments": []}):
            result = await self.runtime._cap_mcp_call("test", "mcp.call", arguments)
            self.assertFalse(result["success"])
        self.service.ensure_initialized.assert_not_awaited()

    async def test_host_error_keeps_plugin_response_contract(self):
        self.service.call_tool_invocation.return_value = SimpleNamespace(
            success=False, content="", error_message="not connected",
            structured_content=None, content_items=[], metadata={},
        )
        result = await self.runtime._cap_mcp_call("test", "mcp.call", {"tool_name": "tool"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "not connected")


if __name__ == "__main__":
    unittest.main()
