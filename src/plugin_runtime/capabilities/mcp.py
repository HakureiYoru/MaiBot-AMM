"""插件运行时的 MCP 调用能力。"""

from typing import Any, Dict

from src.common.logger import get_logger
from src.config.config import config_manager
from src.core.tooling import ToolContentItem, ToolInvocation
from src.mcp_module.service import MCPService, get_mcp_service

logger = get_logger("plugin_runtime.integration")


class RuntimeMCPCapabilityMixin:
    """向显式声明权限的插件提供主程序 MCP 客户端。"""

    async def _get_plugin_mcp_service(self) -> MCPService:
        """复用主程序 MCP 服务，连接与热重载由主程序统一管理。"""

        service = get_mcp_service()
        await service.ensure_initialized(config_manager.get_global_config().mcp)
        return service

    @staticmethod
    def _serialize_mcp_content_item(item: ToolContentItem) -> Dict[str, Any]:
        """把统一工具内容转换为可通过插件 RPC 传输的字典。"""

        return {
            "content_type": item.content_type,
            "text": item.text,
            "data": item.data,
            "mime_type": item.mime_type,
            "uri": item.uri,
            "name": item.name,
            "description": item.description,
            "metadata": dict(item.metadata),
        }

    async def _cap_mcp_call(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """调用主程序已配置并连接的 MCP 工具。"""

        del capability
        tool_name = str(args.get("tool_name", "") or "").strip()
        arguments = args.get("arguments", {})
        if not tool_name:
            return {"success": False, "error": "缺少必要参数 tool_name"}
        if not isinstance(arguments, dict):
            return {"success": False, "error": "参数 arguments 必须为字典"}

        try:
            service = await self._get_plugin_mcp_service()
            result = await service.call_tool_invocation(
                ToolInvocation(tool_name=tool_name, arguments=arguments)
            )
        except Exception as exc:
            logger.error(f"[cap.mcp.call] 插件 {plugin_id} 调用 {tool_name} 失败: {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

        return {
            "success": result.success,
            "content": result.content,
            "error": result.error_message,
            "structured_content": result.structured_content,
            "content_items": [self._serialize_mcp_content_item(item) for item in result.content_items],
            "metadata": dict(result.metadata),
        }

    async def _cap_mcp_list_tools(
        self,
        plugin_id: str,
        capability: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """列出主程序当前可供插件调用的 MCP 工具。"""

        del plugin_id, capability, args
        try:
            service = await self._get_plugin_mcp_service()
            return {
                "success": True,
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters_schema or {"type": "object", "properties": {}},
                        "provider_name": tool.provider_name,
                    }
                    for tool in await service.list_tools()
                ],
            }
        except Exception as exc:
            logger.error(f"[cap.mcp.list_tools] 获取 MCP 工具失败: {exc}", exc_info=True)
            return {"success": False, "error": str(exc), "tools": []}
