"""One triage: the prompt Claude gets, what it cost, the reviewer's error text, and the CLI."""

import csv
import os
import re
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from dss import __main__ as cli
from dss import runner, server, slack, triage


def reply(text: str = '  **DSS-1234 · Plot is wrong**  ', **usage) -> dict:
	return {'result': text, 'total_cost_usd': 0.25, 'usage': usage}


def test_prompt_carries_the_ticket_its_time_and_the_workflow_post():
	prompts = []

	def ask(prompt, trace=None):
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
	assert 'workflow posted' not in prompts[1] and 'slack_thread' not in prompts[1]
	triage.triage('DSS-1236', '2026-10-21_09-30-00', post, ask=ask, thread_ts='1792523850.5')
	line = '\n\nIts Slack thread, with the replies and screenshots: `slack_thread("1792523850.5")`.'
	assert line in prompts[2]
	assert prompts[2].index('slack_thread("1792523850.5")') < prompts[2].index('workflow posted')


def test_each_triage_adds_a_usage_row(tmp_path):
	def ask(prompt, trace=None):
		answer = reply(input_tokens=1000, output_tokens=200, cache_read_input_tokens=30000)
		return {**answer, 'num_turns': 12}

	answer = triage.triage('DSS-1234', '2026-10-21_09-30-00', ask=ask)
	triage.triage('DSS-1235', '2026-10-21_10-00-00', ask=ask)
	assert answer['turns'] == 12
	with (tmp_path / 'usage.csv').open(encoding='utf-8', newline='') as file:
		rows = list(csv.DictReader(file))  # one header, then a row per triage
	assert [row['key'] for row in rows] == ['DSS-1234', 'DSS-1235']
	first = rows[0]
	assert first['posted'] == '2026-10-21_09-30-00' and first['turns'] == '12'
	assert (first['input_tokens'], first['output_tokens']) == ('1000', '200')
	assert first['cache_read_input_tokens'] == '30000'
	assert first['cache_creation_input_tokens'] == '0'
	assert first['cost_usd'] == '0.25' and first['seconds'] == '0'
	assert re.fullmatch(r'\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d', first['run_at'])


def test_trace_file_is_named_only_with_dss_trace(tmp_path, monkeypatch):
	traces = []

	def ask(prompt, trace=None):
		traces.append(trace)
		return reply()

	triage.triage('DSS-1234', '2026-10-21_09-30-00', ask=ask)
	monkeypatch.setenv('DSS_TRACE', '1')
	triage.triage('DSS-1234', '2026-10-21_09-30-00', ask=ask)
	assert traces[0] is None
	assert traces[1].parent == tmp_path / 'trace' and traces[1].parent.is_dir()
	assert re.fullmatch(r'DSS-1234_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d\.jsonl', traces[1].name)


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

	def run_reply(prompt, model=runner.MODEL, trace=None):
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
	assert '0 turns · 1,234 tokens · ≈ $0.25' in err
	((prompt, model),) = claude
	assert 'New ticket DSS-1234, posted 2026-10-21_09-30-00' in prompt and model == 'm1'


def test_cli_gives_claude_the_post_file(claude, monkeypatch, tmp_path, capsys):
	post = tmp_path / 'post.txt'
	post.write_text('created DSS-1234 · ACME · DVT1234', encoding='utf-8')
	run_cli(monkeypatch, 'triage', 'DSS-1234', '--post-file', str(post))
	((prompt, _model),) = claude
	assert prompt.endswith('workflow posted:\ncreated DSS-1234 · ACME · DVT1234')
	with pytest.raises(SystemExit) as stopped:
		run_cli(monkeypatch, 'triage', 'DSS-1234', '--post-file', str(tmp_path / 'missing.txt'))
	assert stopped.value.code == 2 and len(claude) == 1  # Claude is not asked without the post
	assert 'cannot read --post-file' in capsys.readouterr().err


def test_cli_reads_the_thread_as_the_bot_would_have(claude, monkeypatch):
	link = 'https://acme.slack.com/archives/C0793LJ1KDM/p1792523850000500?thread_ts=1.2'
	for thread in (link, '1792523850.000500'):
		run_cli(monkeypatch, 'triage', 'DSS-1234', '--thread', thread)
		prompt, _model = claude[-1]
		assert '`slack_thread("1792523850.000500")`' in prompt
		posted = datetime.fromtimestamp(1792523850.0005).strftime(triage.TIME_FORMAT)
		assert f'posted {posted} (local time)' in prompt  # the post's own time
		until = float(os.environ['DSS_THREAD_UNTIL'])
		assert until == pytest.approx(1792523850.0005 + slack.TRIAGE_DELAY_S)
	run_cli(monkeypatch, 'triage', 'DSS-1234', '--thread', link, '--posted', '2026-10-21_09-30-00')
	assert 'posted 2026-10-21_09-30-00' in claude[-1][0]


def test_cli_rejects_bad_input_before_asking_claude(claude, monkeypatch, capsys):
	bad = (
		('DSS 1234',),
		('DSS-1234', '--posted', '2026-10-21 09:30'),
		('DSS-1234', '--thread', 'https://acme.slack.com/archives/C0793LJ1KDM'),
	)
	for args in bad:
		with pytest.raises(SystemExit) as stopped:
			run_cli(monkeypatch, 'triage', *args)
		assert stopped.value.code == 2
	err = capsys.readouterr().err
	assert "'DSS 1234' is not an issue key" in err and '--posted must look like' in err
	assert 'is not a Slack permalink or a ts' in err
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
	def run_reply(prompt, model, trace=None):
		raise FileNotFoundError('claude')

	monkeypatch.setattr(cli, 'load_env', lambda: None)
	monkeypatch.setattr(runner, 'run_reply', run_reply)
	with pytest.raises(SystemExit, match='The Claude Code CLI is not installed here'):
		run_cli(monkeypatch, 'triage', 'DSS-1234')
