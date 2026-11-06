"""Shared test helpers."""

import asyncio

import pytest
from mcp import Client

from dss import record, triage
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


@pytest.fixture(autouse=True)
def private_output(tmp_path, monkeypatch):
	"""Usage rows, traces and trials go to the test's folder, never to the developer's `out/`."""
	monkeypatch.setattr(triage, 'USAGE_FILE', tmp_path / 'usage.csv')
	monkeypatch.setattr(triage, 'TRACE_DIR', tmp_path / 'trace')
	monkeypatch.setattr(record, 'TRIAL_DIR', tmp_path / 'trial')
	for name in ('DSS_TRACE', 'DSS_TRACE_FILE', 'DSS_STEP_ORDER', 'DSS_MATLAB_GIT'):
		monkeypatch.delenv(name, raising=False)
	# the CLI sets this one itself; setting it here makes monkeypatch remove it after the test
	monkeypatch.setenv('DSS_THREAD_UNTIL', '')
