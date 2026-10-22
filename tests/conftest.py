"""Shared test helpers."""

import asyncio

import pytest
from mcp import Client

from dss.server import build


def call_tool(name: str, arguments: dict, server=None) -> tuple[bool, str]:
	"""Whether one tool call failed, and its text, through an MCP client as Claude makes it.

	*server* defaults to the `dss` server.
	"""
	if server is None:
		server = build()

	async def call():
		async with Client(server) as client:
			return await client.call_tool(name, arguments)

	result = asyncio.run(call())
	text = ' '.join(getattr(part, 'text', '') for part in result.content)
	return result.is_error, text


@pytest.fixture
def mcp_call():
	""":func:`call_tool`, for tests that call tools the way Claude does."""
	return call_tool
