"""MCP server `dss`: the triage's read-only tools, one module per source.

``python -m dss mcp`` serves them over stdio; the runner starts it for every triage. To try the
tools by hand, add it to Claude Code::

	claude mcp add dss -- uv run -q --directory <this repo> python -m dss mcp
"""

import functools
import importlib

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

MODULES = ('jira', 'cerb', 'code', 'confluence')


def build() -> MCPServer:
	"""The `dss` server with every module's tools registered."""
	server = MCPServer('dss', instructions=f'DarkVision DSS ticket triage: {", ".join(MODULES)}.')
	tools = ReportingTools(server)
	for name in MODULES:
		importlib.import_module(f'dss.{name}').register(tools)
	return server


class ReportingTools:
	"""Registers tools on *server* so that a failing tool's message reaches Claude.

	The MCP SDK answers any exception other than `ToolError` with only "Error executing tool
	<name>", which would hide messages such as "Jira is not set up on this machine".
	"""

	def __init__(self, server: MCPServer) -> None:
		self.server = server

	def tool(self, *args, **kwargs):
		"""The server's `tool()` decorator, with the tool's exceptions raised as `ToolError`."""
		register = self.server.tool(*args, **kwargs)

		def decorate(function):
			@functools.wraps(function)
			def reported(*call_args, **call_kwargs):
				try:
					return function(*call_args, **call_kwargs)
				except ToolError:
					raise
				except Exception as error:
					raise ToolError(f'{type(error).__name__}: {error}') from error

			return register(reported)

		return decorate
