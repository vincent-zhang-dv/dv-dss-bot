"""The locked-down runner, the MCP server's tools, the venv worker and the CLI's .env file."""

import asyncio
import json
import os
import subprocess
import sys

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from dss import cerb, runner
from dss.__main__ import load_env
from dss.runner import command
from dss.server import TRACE_CHARS, ReportingTools, build
from dss.worker import run_in


def test_claude_is_locked_down():
	line = command()
	assert line[line.index('--tools') + 1] == ''  # dss tools only, not even Read
	assert line[line.index('--setting-sources') + 1] == ''  # no user/project settings
	assert {'--strict-mcp-config', '--disable-slash-commands', '--no-session-persistence'} <= set(
		line
	)
	assert line[line.index('--permission-mode') + 1] == 'dontAsk'
	assert line[line.index('--allowedTools') + 1] == 'mcp__dss__*'
	assert '"dss"' in line[line.index('--mcp-config') + 1]
	assert 'Gate A' in line[line.index('--system-prompt') + 1]


def test_run_reply_sends_the_prompt_and_reports_failures(tmp_path, monkeypatch):
	calls = []
	outcome: dict = {'returncode': 0, 'stdout': json.dumps({'result': 'Analysis', 'usage': {}})}

	def run(line, **options):
		calls.append((line, options))
		return subprocess.CompletedProcess(line, outcome['returncode'], outcome['stdout'], 'boom')

	monkeypatch.setattr(runner, 'RUN_DIR', tmp_path / 'run')
	monkeypatch.setattr(runner.subprocess, 'run', run)
	assert runner.run_reply('New ticket DSS-1234', model='claude-test')['result'] == 'Analysis'
	line, options = calls[0]
	assert line[line.index('--model') + 1] == 'claude-test'
	assert line[line.index('--effort') + 1] == 'high'
	assert options['input'] == 'New ticket DSS-1234'  # on stdin, not on the command line
	assert options['cwd'] == tmp_path / 'run' and (tmp_path / 'run').is_dir()
	assert options['env']['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] == '1'
	outcome['stdout'] = json.dumps({'is_error': True, 'subtype': 'error_max_turns'})
	with pytest.raises(RuntimeError, match='without an answer: error_max_turns'):
		runner.run_reply('New ticket DSS-1234')
	outcome['returncode'] = 1
	with pytest.raises(RuntimeError, match=r'claude -p failed \(1\): boom'):
		runner.run_reply('New ticket DSS-1234')



def test_run_reply_names_the_trace_file_only_when_asked(tmp_path, monkeypatch):
	envs = []

	def run(line, **options):
		envs.append(options['env'])
		return subprocess.CompletedProcess(line, 0, json.dumps({'result': 'Analysis'}), '')

	monkeypatch.setattr(runner, 'RUN_DIR', tmp_path / 'run')
	monkeypatch.setattr(runner.subprocess, 'run', run)
	monkeypatch.setenv('DSS_TRACE_FILE', str(tmp_path / 'stale.jsonl'))
	runner.run_reply('New ticket DSS-1234', trace=tmp_path / 'DSS-1234.jsonl')
	runner.run_reply('New ticket DSS-1234')
	assert envs[0]['DSS_TRACE_FILE'] == str(tmp_path / 'DSS-1234.jsonl')
	assert 'DSS_TRACE_FILE' not in envs[1]  # an inherited value never leaks into another triage


def test_server_serves_the_triage_tools():
	tools = {tool.name: tool for tool in asyncio.run(build().list_tools())}
	assert set(tools) == {
		'jira_get_issue',
		'jira_search',
		'cerb_client_info',
		'cerb_find_project',
		'cerb_list',
		'cerb_read_text',
		'cerb_last_session',
		'cerb_prod_version',
		'code_grep',
		'code_read',
		'code_log_s',
		'confluence_search',
		'confluence_page',
	}
	finder = tools['cerb_find_project']  # registration keeps each tool's parameters and help
	assert list(finder.input_schema['properties']) == ['dvt']
	assert finder.description is not None
	assert finder.description.startswith('Project folders of DVT')


def test_reporting_tools_keep_results_and_explain_failures(mcp_call):
	server = MCPServer('test')
	tools = ReportingTools(server)

	@tools.tool()
	def echo(text: str) -> str:
		"""Echo *text*."""
		return text

	@tools.tool()
	def broken() -> str:
		"""Fail as a bug would."""
		raise KeyError('dvt')

	@tools.tool()
	def refused() -> str:
		"""Fail on purpose."""
		raise ToolError('not allowed')

	assert mcp_call('echo', {'text': 'hi'}, server) == (False, 'hi')
	assert mcp_call('broken', {}, server) == (True, "Error executing tool broken: KeyError: 'dvt'")
	assert mcp_call('refused', {}, server) == (True, 'Error executing tool refused: not allowed')



def test_reporting_tools_trace_each_call_when_the_runner_asks(tmp_path, monkeypatch, mcp_call):
	server = MCPServer('test')
	tools = ReportingTools(server)

	@tools.tool()
	def echo(text: str) -> str:
		"""Echo *text*."""
		return text

	@tools.tool()
	def facts(dvt: int) -> dict:
		"""A long dict result."""
		return {'dvt': dvt, 'notes': 'x' * 3000}

	@tools.tool()
	def broken() -> str:
		"""Fail as a bug would."""
		raise KeyError('dvt')

	@tools.tool()
	def refused() -> str:
		"""Fail on purpose."""
		raise ToolError('not allowed')

	mcp_call('echo', {'text': 'hi'}, server)  # no DSS_TRACE_FILE: nothing is written
	assert list(tmp_path.iterdir()) == []
	trace = tmp_path / 'DSS-1234.jsonl'
	monkeypatch.setenv('DSS_TRACE_FILE', str(trace))
	for name, arguments in (('echo', {'text': 'hé'}), ('facts', {'dvt': 1234})):
		mcp_call(name, arguments, server)
	mcp_call('broken', {}, server)
	mcp_call('refused', {}, server)
	lines = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()]
	assert [line['tool'] for line in lines] == ['echo', 'facts', 'broken', 'refused']
	assert all(line['seconds'] >= 0 for line in lines)
	assert lines[0]['arguments'] == {'text': 'hé'}
	assert (lines[0]['chars'], lines[0]['result']) == (2, 'hé')
	assert lines[1]['arguments'] == {'dvt': 1234}
	assert lines[1]['chars'] > TRACE_CHARS and len(lines[1]['result']) == TRACE_CHARS  # capped
	assert lines[2]['error'] == "KeyError: 'dvt'" and 'result' not in lines[2]
	assert lines[3]['error'] == 'ToolError: not allowed'


def test_tool_errors_reach_claude_with_their_message(tmp_path, monkeypatch, mcp_call):
	for name in ('DV_PROJECT_INDEX_USER', 'DV_PROJECT_INDEX_PASSWORD'):
		monkeypatch.delenv(name, raising=False)
	failed, text = mcp_call('cerb_find_project', {'dvt': 1234})
	assert failed and 'set DV_PROJECT_INDEX_USER, DV_PROJECT_INDEX_PASSWORD' in text
	(tmp_path / 'cerberus-1.0.4-py3-none-any.whl').touch()
	monkeypatch.setenv('DV_CERB_PROD_DIR', str(tmp_path))
	assert mcp_call('cerb_prod_version', {}) == (False, '1.0.4')


def test_client_info_copies_a_share_file_before_cerberus_reads_it(tmp_path, monkeypatch, mcp_call):
	copies, reads = [], []

	def local_copy(path):
		copies.append(path)
		return tmp_path / 'copy.xlsm'

	def run_cerberus(app, module, func, **kwargs):
		reads.append((app, module, func, kwargs['path']))
		return {'version': '1.6'}

	monkeypatch.setattr(cerb, 'local_copy', local_copy)
	monkeypatch.setattr(cerb, 'run_in', run_cerberus)
	share_file = r'\\nas\projects\ACME\DVT1234_x\DVT1234_client_info.xlsm'
	failed, _ = mcp_call('cerb_client_info', {'path': share_file})
	assert not failed
	mcp_call('cerb_client_info', {'path': str(tmp_path / 'local.xlsm')})
	assert copies == [share_file]
	assert reads == [
		('cerb', 'dss.cerb', 'client_info', str(tmp_path / 'copy.xlsm')),
		('cerb', 'dss.cerb', 'client_info', str((tmp_path / 'local.xlsm').resolve())),
	]


def test_worker_runs_a_function_in_another_python(monkeypatch):
	monkeypatch.setenv('DSS_CERB_PY', sys.executable)
	monkeypatch.setenv('DSS_CERB_CWD', '.')
	assert run_in('cerb', 'json', 'dumps', obj=[1, 'a']) == '[1, "a"]'
	with pytest.raises(RuntimeError, match=r'json.nope failed'):
		run_in('cerb', 'json', 'nope')


def test_worker_says_which_setting_is_missing(monkeypatch):
	monkeypatch.delenv('DSS_CERB_PY', raising=False)
	with pytest.raises(RuntimeError, match='set DSS_CERB_PY'):
		run_in('cerb', 'json', 'dumps', obj=1)
	with pytest.raises(ValueError, match="unknown app 'nope'"):
		run_in('nope', 'json', 'dumps', obj=1)


def test_env_file_fills_in_only_what_is_not_set(tmp_path, monkeypatch):
	env = tmp_path / '.env'
	env.write_text(
		'# comment\nDSS_T_NEW="C:\\a b"\nDSS_T_SET=from file\nDSS_T_EMPTY=\n', encoding='utf-8'
	)
	monkeypatch.setenv('DSS_T_SET', 'from env')
	monkeypatch.delenv('DSS_T_NEW', raising=False)
	monkeypatch.delenv('DSS_T_EMPTY', raising=False)
	load_env(env)
	assert os.environ['DSS_T_NEW'] == 'C:\\a b' and os.environ['DSS_T_SET'] == 'from env'
	assert 'DSS_T_EMPTY' not in os.environ
	monkeypatch.delenv('DSS_T_NEW')
	load_env(tmp_path / 'missing.env')  # no file is not an error
