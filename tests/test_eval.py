"""Scoring saved answers against the golden set, on the made-up example row."""

import json
import subprocess
import sys

import pytest

from dss import code, confluence, eval, jira, runner
from dss.worker import ROOT

EXAMPLE = ROOT / 'docs' / 'golden.example.jsonl'
ANSWER = """**DSS-1234 · Deliverable shows old bookmarks**
ACME, DVT1234, reporter Ann Analyst, Toolbox Name CERB-PE, Cerberus 1.0.3

**Triage**
Record, Gate A and Gate B passed. Stopped at Gate C.

**Checked**
1. `cerb_list` shows two folders, `Bookmarks` and `Bookmarks_old`, for job 2.
2. `deliverables/bookmarks.py:42` reads the first folder that starts with `Bookmarks`.
3. Same cause as DSS-1100; DTN-3000 tracks the fix; see Confluence page 123456789.

**Not proved**
- Whether the client info is corrupt: not read.

**Suggested reply to the analyst**
Delete `Bookmarks_old` and rerun.

**Suggested labels**
Toolbox Name: CERB-PE · Investigation Outcome: User Error

**Confidence:** proved.
"""
CALLS = [
	{'tool': 'jira_get_issue', 'arguments': {'key': 'DSS-1234'}, 'result': '{}'},
	{'tool': 'cerb_find_project', 'arguments': {'dvt': 1234}, 'result': '{}'},
	{'tool': 'code_grep', 'arguments': {'pattern': 'Bookmarks'}, 'result': 'Cerberus at abc1234, x'},
]


def fake_resolver(missing=()):
	"""A resolver that knows every citation except those in *missing*; records what it saw."""
	seen = []

	def resolve(kind, value, ref):
		seen.append((kind, value, ref))
		return value not in missing

	resolve.seen = seen
	return resolve


def test_example_row_loads():
	rows = eval.load_golden(EXAMPLE)
	assert len(rows) == 1
	assert rows[0]['key'] == 'DSS-1234' and rows[0]['gate'] in eval.GATES


@pytest.mark.parametrize(
	('line', 'message'),
	[
		('not json', 'line 2: not JSON'),
		('{"key": "DSS-1"}', 'line 2: no posted, gate, outcome'),
		('{"key": "DSS-1", "posted": "x", "gate": "D", "outcome": "y"}', 'gate must be one of'),
	],
)
def test_bad_golden_line_is_named(tmp_path, line, message):
	path = tmp_path / 'golden.jsonl'
	path.write_text(EXAMPLE.read_text(encoding='utf-8') + line + '\n', encoding='utf-8')
	with pytest.raises(ValueError, match=message):
		eval.load_golden(path)


def test_sections_split_on_the_answer_headers():
	parts = eval.sections(ANSWER)
	assert sorted(parts) == [
		'checked',
		'confidence',
		'not proved',
		'suggested labels',
		'suggested reply',
		'triage',
	]
	assert 'Bookmarks_old' in parts['checked'] and 'corrupt' not in parts['checked']
	assert parts['confidence'].startswith('**Confidence:** proved.')


def test_evidence_counts_as_read_from_the_trace_arguments():
	assert eval.was_read('jira_get_issue:DSS-1234', CALLS)
	assert eval.was_read('cerb_find_project:1234', CALLS)
	assert eval.was_read('bookmarks', CALLS)  # any tool, any case
	assert not eval.was_read('cerb_list:DVT1234', CALLS)
	assert not eval.was_read('jira_search:DSS-1234', CALLS)  # read, but by another tool
	windows = [{'tool': 'cerb_list', 'arguments': {'folder': '\\\\server\\projects\\DVT1234'}}]
	assert eval.was_read('cerb_list://server/projects/dvt1234', windows)


def test_citations_under_checked():
	checked = eval.sections(ANSWER)['checked']
	assert eval.citations(checked) == [
		('jira', 'DSS-1100'),
		('jira', 'DTN-3000'),
		('confluence', '123456789'),
		('code', ('deliverables/bookmarks.py', 42)),
	]
	assert eval.citations('UTF-8 text, ISO-8601 dates, DSS-1 and DSS-1') == [('jira', 'DSS-1')]


def test_score_on_the_example():
	row = eval.load_golden(EXAMPLE)[0]
	resolve = fake_resolver(missing={'DTN-3000'})
	result = eval.score(row, ANSWER, CALLS, resolve)
	assert result['evidence_read'] == {
		'jira_get_issue:DSS-1234': True,
		'cerb_find_project:1234': True,
		'cerb_list:DVT1234': False,
	}
	assert [citation['resolves'] for citation in result['citations']] == [True, False, True, True]
	assert resolve.seen[0][2] == 'abc1234'  # the commit the run read, from its trace
	assert result['false_leads'] == ['client info is corrupt']  # anywhere, Not proved too
	assert result['labels_named'] == {'toolbox': True, 'investigation_outcome': True}
	assert result['under_limit'] and result['chars'] == len(ANSWER)


def test_prod_release_is_looked_up_only_for_code_citations(monkeypatch):
	monkeypatch.setattr(code, 'prod_ref', lambda: pytest.fail('looked up the prod release'))
	row = eval.load_golden(EXAMPLE)[0]
	answer = ANSWER.replace('deliverables/bookmarks.py:42', 'the bookmarks code')
	eval.score(row, answer, [], fake_resolver())


def test_resolver_reads_not_found_as_unresolved_and_raises_setup_errors(monkeypatch):
	def get_issue(key):
		if key == 'DSS-9':
			raise jira.JiraError('no such ticket (or no access to it)')
		raise jira.JiraError('the Jira token was refused')

	monkeypatch.setattr(jira, 'get_issue', get_issue)
	monkeypatch.setattr(confluence, 'page', lambda page_id: {'id': page_id})
	resolve = eval.Resolver()
	assert resolve('jira', 'DSS-9', '') is False
	assert resolve('confluence', '123456', '') is True
	with pytest.raises(jira.JiraError, match='token'):
		resolve('jira', 'DSS-1', '')


def test_code_line_matches_the_path_end_and_the_line_count(monkeypatch):
	monkeypatch.setattr(code, 'files', lambda ref: ['cerberus/deliverables/bookmarks.py'])
	monkeypatch.setattr(code, 'line_count', lambda path, ref: 50)
	resolve = eval.Resolver()
	assert resolve('code', ('deliverables/bookmarks.py', 42), 'abc1234')
	assert not resolve('code', ('deliverables/bookmarks.py', 51), 'abc1234')
	assert not resolve('code', ('other.py', 1), 'abc1234')


def test_judge_reads_the_json_verdict():
	prompts = []

	def ask(prompt):
		prompts.append(prompt)
		return 'Sure. {"match": true, "reason": "Both name the stale Bookmarks folder."}'

	row = eval.load_golden(EXAMPLE)[0]
	assert eval.judge(row, ANSWER, ask) == {
		'match': True,
		'reason': 'Both name the stale Bookmarks folder.',
	}
	assert prompts[0].startswith('Known resolution of DSS-1234: Job 2 has two Bookmarks')
	assert eval.judge(row, ANSWER, lambda prompt: 'no idea')['match'] is None


def test_judge_runs_claude_with_no_tools():
	line = eval.judge_command()
	assert line[line.index('--model') + 1] == eval.JUDGE_MODEL
	assert line[line.index('--tools') + 1] == ''
	assert line[line.index('--mcp-config') + 1] == '{"mcpServers": {}}'
	assert '--strict-mcp-config' in line and '--allowedTools' not in line
	assert line[line.index('--permission-mode') + 1] == 'dontAsk'


def test_run_scores_every_row_and_saves_them(tmp_path):
	golden = tmp_path / 'golden.jsonl'
	other = {'key': 'DSS-1235', 'posted': '2026-10-22_10-00-00', 'gate': 'B', 'outcome': 'x'}
	golden.write_text(
		EXAMPLE.read_text(encoding='utf-8') + json.dumps(other) + '\n', encoding='utf-8'
	)
	(tmp_path / 'DSS-1234.md').write_text(ANSWER, encoding='utf-8')
	trace = '\n'.join(json.dumps(call) for call in CALLS)
	(tmp_path / 'DSS-1234_2026-10-30_10-00-00.jsonl').write_text(trace, encoding='utf-8')
	scores = eval.run(golden, tmp_path, resolve=fake_resolver())
	assert scores[0]['evidence_read']['cerb_find_project:1234']
	assert scores[1] == {'key': 'DSS-1235', 'gate': 'B', 'missing': True}
	saved = (tmp_path / 'scores.jsonl').read_text(encoding='utf-8').splitlines()
	assert [json.loads(line)['key'] for line in saved] == ['DSS-1234', 'DSS-1235']
	assert eval.summary(scores[0]).startswith('DSS-1234   gate C        read 2/3 · citations 4/4')
	assert eval.summary(scores[1]).endswith('no answer saved')


def test_run_without_judge_starts_no_claude(tmp_path, monkeypatch):
	monkeypatch.setattr(runner, 'run_reply', lambda *args, **kwargs: pytest.fail('ran claude'))
	monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: pytest.fail('ran a command'))
	monkeypatch.setattr(code, 'prod_ref', lambda: 'abc1234')  # no trace saved
	(tmp_path / 'DSS-1234.md').write_text(ANSWER, encoding='utf-8')
	scores = eval.run(EXAMPLE, tmp_path, resolve=fake_resolver())
	assert 'judge' not in scores[0]


def test_cli_eval_prints_a_line_per_ticket(tmp_path, monkeypatch, capsys):
	from dss import __main__ as cli

	(tmp_path / 'DSS-1234.md').write_text(ANSWER, encoding='utf-8')
	monkeypatch.setattr(eval, 'Resolver', lambda: fake_resolver())
	monkeypatch.setattr(code, 'prod_ref', lambda: 'abc1234')  # no trace saved
	monkeypatch.setattr(cli, 'load_env', lambda: None)
	monkeypatch.setattr(sys, 'argv', ['dss', 'eval', str(EXAMPLE), str(tmp_path)])
	cli.main()
	assert capsys.readouterr().out.startswith('DSS-1234   gate C')
	monkeypatch.setattr(sys, 'argv', ['dss', 'eval', str(tmp_path / 'none.jsonl'), str(tmp_path)])
	with pytest.raises(SystemExit, match='cannot score'):
		cli.main()
