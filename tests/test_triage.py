"""One triage: the prompt Claude gets, what it cost, the reviewer's error text, and the CLI."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from dss import __main__ as cli
from dss import runner, server, slack, triage


def reply(text: str = '  **DSS-1234 · Plot is wrong**  ', **usage) -> dict:
	return {'result': text, 'total_cost_usd': 0.25, 'usage': usage}


def test_prompt_carries_the_ticket_its_time_and_the_workflow_post():
	prompts = []

	def ask(prompt):
		prompts.append(prompt)
		return reply(input_tokens=1000, output_tokens=200, cache_read_input_tokens=30000)

	post = 'created DSS-1234\n' + 'x' * 9000
	answer = triage.triage('DSS-1234', '2026-10-21_09-30-00', post, ask=ask)
	assert prompts[0].startswith('New ticket DSS-1234, posted 2026-10-21_09-30-00 (local time).')
	assert '\n\nThe support-request workflow posted:\ncreated DSS-1234\n' in prompts[0]
	assert prompts[0].endswith(post[: triage.MAX_POST])  # a long post is cut, not dropped
	assert answer['text'] == '**DSS-1234 · Plot is wrong**'
	assert answer['tokens'] == 31200 and answer['cost_usd'] == 0.25
	assert isinstance(answer['seconds'], int)
	triage.triage('DSS-1235', '2026-10-21_09-30-00', ask=ask)
	assert 'workflow posted' not in prompts[1]


def test_spend_adds_every_token_kind_and_tolerates_missing_usage():
	assert triage.spend({'usage': {'input_tokens': 5, 'output_tokens': None}}) == {
		'cost_usd': 0.0,
		'tokens': 5,
	}
	assert triage.spend({}) == {'cost_usd': 0.0, 'tokens': 0}


@pytest.mark.parametrize(
	('error', 'expected'),
	[
		(FileNotFoundError('claude'), 'The Claude Code CLI is not installed here.'),
		(subprocess.TimeoutExpired('claude', 900), 'Claude took longer than 900 s.'),
		(RuntimeError('Invalid API key · Please run /login'), 'Claude is not logged in here.'),
		(RuntimeError('x' * 600), 'Claude failed: ' + 'x' * 500),
	],
)
def test_explain_tells_the_reviewer_what_to_do(error, expected):
	assert triage.explain(error).startswith(expected)
	assert len(triage.explain(error)) <= len('Claude failed: ') + 500


@pytest.fixture
def claude(monkeypatch):
	"""The CLI with a fake Claude and without the developer's .env file."""
	prompts = []

	def run_reply(prompt, model=runner.MODEL):
		prompts.append((prompt, model))
		return reply(input_tokens=1234)

	monkeypatch.setattr(cli, 'load_env', lambda: None)
	monkeypatch.setattr(runner, 'run_reply', run_reply)
	return prompts


def run_cli(monkeypatch, *args: str) -> None:
	monkeypatch.setattr(sys, 'argv', ['dss', *args])
	cli.main()


def test_cli_prints_the_analysis_and_its_cost(claude, monkeypatch, capsys):
	run_cli(monkeypatch, 'triage', 'DSS-1234', '--posted', '2026-10-21_09-30-00', '--model', 'm1')
	out, err = capsys.readouterr()
	assert out.strip() == '**DSS-1234 · Plot is wrong**'
	assert '1,234 tokens · ≈ $0.25' in err
	((prompt, model),) = claude
	assert 'New ticket DSS-1234, posted 2026-10-21_09-30-00' in prompt and model == 'm1'


def test_cli_rejects_bad_input_before_asking_claude(claude, monkeypatch, capsys):
	for args in (('DSS 1234',), ('DSS-1234', '--posted', '2026-10-21 09:30')):
		with pytest.raises(SystemExit) as stopped:
			run_cli(monkeypatch, 'triage', *args)
		assert stopped.value.code == 2
	err = capsys.readouterr().err
	assert "'DSS 1234' is not an issue key" in err and '--posted must look like' in err
	assert claude == []


def test_cli_starts_the_tool_server_and_the_bot(monkeypatch):
	started = []
	monkeypatch.setattr(cli, 'load_env', lambda: started.append('.env'))
	monkeypatch.setattr(server, 'build', lambda: SimpleNamespace(run=lambda: started.append('mcp')))
	monkeypatch.setattr(slack, 'main', lambda: started.append('slack'))
	run_cli(monkeypatch, 'mcp')
	run_cli(monkeypatch, 'slack')
	assert started == ['.env', 'mcp', '.env', 'slack']  # settings are loaded before either starts


def test_cli_reports_a_failed_triage_in_one_line(monkeypatch):
	def run_reply(prompt, model):
		raise FileNotFoundError('claude')

	monkeypatch.setattr(cli, 'load_env', lambda: None)
	monkeypatch.setattr(runner, 'run_reply', run_reply)
	with pytest.raises(SystemExit, match='The Claude Code CLI is not installed here'):
		run_cli(monkeypatch, 'triage', 'DSS-1234')
