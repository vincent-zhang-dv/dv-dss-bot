"""MCP server `dss`: the triage's read-only tools, one module per source.

``python -m dss mcp`` serves them over stdio; the runner starts it for every triage. To try the
tools by hand, add it to Claude Code::

	claude mcp add dss -- uv run -q --directory <this repo> python -m dss mcp
"""

import functools
import importlib
import json
import os
import time

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

MODULES = ('thread', 'jira', 'cerb', 'code', 'confluence')
TRACE_CHARS = 2000  # result text kept per call in a trace line


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
	When DSS_TRACE_FILE names a file, each call also appends one JSON line to it (see
	:func:`trace`).
	"""

	def __init__(self, server: MCPServer) -> None:
		self.server = server

	def tool(self, *args, **kwargs):
		"""The server's `tool()` decorator, with the tool's exceptions raised as `ToolError`."""
		register = self.server.tool(*args, **kwargs)

		def decorate(function):
			@functools.wraps(function)
			def reported(*call_args, **call_kwargs):
				started = time.monotonic()
				try:
					result = function(*call_args, **call_kwargs)
				except ToolError as error:
					trace(function.__name__, call_kwargs, started, error=error)
					raise
				except Exception as error:
					trace(function.__name__, call_kwargs, started, error=error)
					raise ToolError(f'{type(error).__name__}: {error}') from error
				trace(function.__name__, call_kwargs, started, result=result)
				return result

			return register(reported)

		return decorate


def trace(tool: str, arguments: dict, started: float, result=None, error=None) -> None:
	"""Append one tool call to the DSS_TRACE_FILE file, when the runner set one.

	The line has the tool, its arguments and the seconds it took, then either the error or the
	result's size in characters and its first TRACE_CHARS characters.
	"""
	path = os.environ.get('DSS_TRACE_FILE')
	if not path:
		return
	line = {'tool': tool, 'arguments': arguments, 'seconds': round(time.monotonic() - started, 2)}
	if error is not None:
		line['error'] = f'{type(error).__name__}: {error}'
	else:
		text = result
		if not isinstance(result, str):
			text = json.dumps(result, ensure_ascii=False, default=str)
		line['chars'] = len(text)
		line['result'] = text[:TRACE_CHARS]
	with open(path, 'a', encoding='utf-8') as file:
		file.write(json.dumps(line, ensure_ascii=False, default=str) + '\n')
