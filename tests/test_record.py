"""Trial mode: the record code reads, the prompt, the check, the DM and thread, and the CLI."""

import json
import sys
from pathlib import Path

import pytest

from dss import __main__ as cli
from dss import cerb, jira, record, runner

EXAMPLE = json.loads(record.SCHEMA_FILE.with_name('triage_record.example.json').read_text('utf-8'))
PROJECT = r'\\server\projects\ACME\DVT1234_2026-10-20_WI_Well-1'
OTHER = r'\\server\projects\ACME\DVT1234_2026-11-02_PE_Well-1'
ISSUE = {
	'key': 'DSS-1234',
	'created': '2026-10-22T10:00:00.000-0700',
	'reporter': 'Ann Analyst',
	'dvt': 1234.0,
	'toolbox': 'CERB-WI',
	'project_path': PROJECT,
}
POSTED = '2026-10-22_10-00-05'
QUESTION = {'ask': 'Which DVCalipers did the RAID run use?', 'settles': 'whether they match'}


def answer_text(**changes) -> str:
	"""Claude's answer: the example's triage and class blocks, with *changes* to triage."""
	return json.dumps({'triage': {**EXAMPLE['triage'], **changes}, 'class': EXAMPLE['class']})


def claude(text: str, prompts: list):
	"""A fake Claude that answers *text* and keeps the prompts it gets in *prompts*."""

	def ask(prompt, trace=None):
		prompts.append(prompt)
		return {'result': text, 'total_cost_usd': 0.3, 'usage': {'input_tokens': 5000}}

	return ask


@pytest.fixture
def sources(monkeypatch):
	"""Jira, the project index, telemetry and the prod folder, faked; the calls they got."""
	calls = []

	def get_issue(key, fields=jira.TICKET_FIELDS):
		calls.append(('jira', key, 'created' in fields))
		return dict(ISSUE)

	def find_project(dvt):
		calls.append(('index', dvt))
		return {'projects': [PROJECT]}

	def last_session(project, user, before=''):
		calls.append(('telemetry', project, user, before))
		failed = {'name': 'casing_tally', 'status': 'error', 'result_detail': 'KeyError: ID'}
		return {'found': True, 'cerb': '1.0.3', 'failed_runs': [failed], 'last_config': ''}

	monkeypatch.setattr(jira, 'get_issue', get_issue)
	monkeypatch.setattr(cerb, 'find_project', find_project)
	monkeypatch.setattr(cerb, 'last_session', last_session)
	monkeypatch.setattr(cerb, 'prod_version', lambda: '1.0.4')
	monkeypatch.delenv('JIRA_URL', raising=False)
	monkeypatch.delenv('CONFLUENCE_URL', raising=False)
	return calls


def test_code_reads_the_dss_ticket_before_claude_starts(sources):
	ticket, notes = record.build_ticket('DSS-1234', POSTED)
	assert ticket == {
		'key': 'DSS-1234',
		'created': '2026-10-22T10:00:00-07:00',
		'reporter': 'Ann Analyst',
		'dvt': 1234,
		'project': PROJECT,
		'workflow': 'WI',
		'toolbox': 'CERB-WI',
		'cerberus_version': '1.0.3',
		'prod_version': '1.0.4',
		'last_error': 'casing_tally: KeyError: ID',
	}
	assert notes == []
	assert ('jira', 'DSS-1234', True) in sources  # with the created time
	assert ('telemetry', PROJECT, 'Ann Analyst', POSTED) in sources  # the session before the post


def test_a_source_that_fails_is_noted_not_fatal(sources, monkeypatch):
	def refused(key, fields):
		raise jira.JiraError('the Jira token was refused')

	monkeypatch.setattr(jira, 'get_issue', refused)
	ticket, notes = record.build_ticket('DSS-1234', POSTED)
	assert notes == ['Jira: the Jira token was refused']
	assert ticket['reporter'] == 'unknown' and ticket['project'] is None
	assert ticket['created'].startswith('2026-10-22T10:00:05')  # the post time instead
	assert ticket['prod_version'] == '1.0.4'
	full = {'schema': record.SCHEMA_ID, 'dss_ticket': ticket, 'triage': EXAMPLE['triage']}
	assert record.check({**full, 'class': EXAMPLE['class']}) == []

	monkeypatch.setattr(jira, 'get_issue', lambda key, fields: {**ISSUE, 'dvt': None})
	ticket, notes = record.build_ticket('DSS-1234', POSTED)
	assert notes == ['the DSS ticket has no DVT number'] and ticket['workflow'] == 'unknown'


@pytest.mark.parametrize(
	('folders', 'project_path', 'expected'),
	[
		([PROJECT, OTHER], PROJECT, PROJECT),  # Project Path picks one, as triage.md says
		([PROJECT, OTHER], '', None),
		([], PROJECT, None),  # not indexed yet
	],
)
def test_project_path_picks_between_folders(sources, monkeypatch, folders, project_path, expected):
	monkeypatch.setattr(cerb, 'find_project', lambda dvt: {'projects': folders})
	monkeypatch.setattr(
		jira, 'get_issue', lambda key, fields: {**ISSUE, 'project_path': project_path}
	)
	ticket, notes = record.build_ticket('DSS-1234', POSTED)
	assert ticket['project'] == expected
	assert (notes == []) == (expected is not None)


@pytest.mark.parametrize(
	('name', 'expected'),
	[
		('DVT1234_2026-10-20_WI_Well-1', 'WI'),
		('DVT1234_2026-10-20_ff_Well-1', 'FF'),
		('DVT1234_2026-10-20_CI-PE_Well-1', 'combo'),
		('DVT1234_2026-10-20_Survey_Well-1', 'unknown'),
		('DVT1234', 'unknown'),
	],
)
def test_workflow_comes_from_the_folder_name(name, expected):
	assert record.workflow('\\\\server\\projects\\ACME\\' + name) == expected


def test_the_trial_keeps_the_bots_steps_and_asks_for_the_record():
	steps = runner.SYSTEM.read_text(encoding='utf-8')
	prompt = record.system_prompt()
	assert prompt.startswith(steps[: steps.index('Answer in Markdown')])
	assert 'Answer in Markdown' not in prompt and 'Answer with one JSON object' in prompt
	defs = json.loads(record.SCHEMA_FILE.read_text(encoding='utf-8'))['$defs']
	answer = defs['triage']['properties']
	planning = defs['class']['properties']
	lists = [
		answer['status']['enum'],
		answer['confidence']['enum'],
		answer['settled_at']['enum'],
		answer['playbook']['enum'],
		answer['where']['properties']['kind']['enum'],
		answer['do_now']['properties']['who']['enum'],
		answer['escalate']['properties']['kind']['enum'],
		planning['impact']['enum'],
		planning['fix_type']['enum'],
		defs['cause_family']['enum'],
		defs['feature']['enum'],
		defs['prevent']['enum'],
	]
	for values in lists:
		for value in values:
			if value is not None:
				assert f'`{value}`' in prompt
	for cause in json.loads(record.KNOWN_CAUSES_FILE.read_text(encoding='utf-8')):
		assert f'`{cause["slug"]}`' in prompt
	assert '$' not in prompt
	assert len(prompt) < 20_000  # it goes on the claude command line, which Windows caps at 32,767


def test_the_trial_runs_the_same_locked_down_claude(sources, monkeypatch):
	commands = []

	def run_reply(prompt, trace=None, system=''):
		commands.append(runner.command(system=system))
		return {'result': answer_text(), 'total_cost_usd': 0.3, 'usage': {}}

	monkeypatch.setattr(runner, 'run_reply', run_reply)
	record.trial('DSS-1234', POSTED)
	(line,) = commands
	bot = runner.command()
	prompt = line.index('--system-prompt') + 1
	assert line[:prompt] == bot[:prompt] and line[prompt + 1 :] == bot[prompt + 1 :]
	assert line[prompt] == record.system_prompt()


def test_a_trial_checks_and_saves_the_record(sources, tmp_path):
	prompts: list = []
	post = 'created DSS-1234 · ACME · DVT1234'
	result = record.trial('DSS-1234', POSTED, post, ask=claude(answer_text(), prompts))
	assert result['problems'] == []
	assert result['record']['dss_ticket'] == result['ticket']
	assert result['record']['triage'] == EXAMPLE['triage']
	assert result['record']['class'] == EXAMPLE['class']
	(prompt,) = prompts
	assert prompt.startswith(f'New ticket DSS-1234, posted {POSTED} (local time).')
	assert json.dumps(result['ticket'], indent=1) in prompt
	assert prompt.endswith('workflow posted:\n' + post)
	saved = Path(result['file'])
	assert saved.parent == tmp_path / 'trial'  # never the bot's out/answers
	assert json.loads(saved.read_text(encoding='utf-8')) == result


def test_the_dm_decides_and_the_thread_proves_without_repeats(sources):
	result = record.trial('DSS-1234', POSTED, ask=claude(answer_text(), []))
	dm, thread = result['dm'], result['thread']
	head = '**DSS-1234 · proved · high confidence · wrong numbers · input data · CERB-WI**'
	assert dm.startswith(head)
	answer = EXAMPLE['triage']
	for text in (answer['what'], answer['cause'], answer['do_now']['action'], answer['reply']):
		assert dm.count(text) == 1 and text not in thread
	for item in answer['evidence']:
		assert thread.count(item['fact']) == 1 and item['fact'] not in dm
	assert '**Where:** data file `DVT1234_client_info.xlsm`, Casing Record row 3' in dm
	assert f'> {answer["reply"]}' in dm
	assert '- reported: 19.97% (Defect Summary, joint 57)' in thread
	assert '- first difference: casing ID' in thread
	assert '**Labels to set:** Outcome User Error' in thread  # the Toolbox Name is already set
	assert 'Cerberus 1.0.3 · prod 1.0.4 · last failed run: casing\\_tally: KeyError: ID' in thread
	assert 'input data' not in thread  # the cause family is in the DM's first line
	assert dm.endswith('DSS-1234 · 5,000 tokens · ≈ $0.30 · 0 s_')


def test_claude_text_shows_as_written(sources):
	cause = (
		r'The page path <project>\cerberus\<scene>\x.pdf is too long, so __init__ fails at `a.py:9`'
	)
	text = answer_text(cause=cause, reply='Rerun it.\nThanks')
	dm = record.trial('DSS-1234', POSTED, ask=claude(text, []))['dm']
	assert r'\<project\>\\cerberus\\\<scene\>\\x.pdf' in dm  # Markdown would eat \< and <...>
	assert r'so \_\_init\_\_ fails at `a.py:9`' in dm  # code spans stay as they are
	assert '> Rerun it.\n> Thanks\n\n_Trial' in dm  # the footer is not part of the quoted reply


def test_reply_is_not_part_of_the_escalation_draft(sources):
	escalate = {'kind': 'bug', 'draft': 'The header text overlaps.'}
	text = answer_text(escalate=escalate, reply='Rerun it.')
	dm = record.trial('DSS-1234', POSTED, ask=claude(text, []))['dm']
	assert '> The header text overlaps.\n\n**Suggested reply:**\n> Rerun it.' in dm


def test_empty_fields_are_left_out(sources):
	text = answer_text(
		status='needs_info',
		confidence='low',
		settled_at='gate_c',
		playbook=None,
		cause=None,
		where=None,
		evidence=[],
		do_now=None,
		reply=None,
		details={},
		questions=[QUESTION],
	)
	result = record.trial('DSS-1234', POSTED, ask=claude(text, []))
	assert result['problems'] == []
	dm = result['dm']
	assert dm.startswith('**DSS-1234 · needs info · low confidence · Gate C · input data')
	assert f'1. {QUESTION["ask"]} (settles: {QUESTION["settles"]})' in dm
	for label in ('Cause:', 'Where:', 'Do now', 'Suggested reply'):
		assert label not in dm
	assert 'Evidence' not in result['thread'] and 'Details' not in result['thread']


def test_the_record_may_come_in_a_code_fence(sources):
	text = f'Here is the record:\n```json\n{answer_text()}\n```'
	assert record.trial('DSS-1234', POSTED, ask=claude(text, []))['problems'] == []


@pytest.mark.parametrize(
	('text', 'problem'),
	[
		(answer_text(evidence=[]), 'triage.evidence:'),  # proved needs evidence
		(answer_text(status='likely', not_proved=[]), 'triage.not_proved:'),
		('I could not finish the triage.', 'the answer has no JSON object'),
		('{"triage": }', 'the answer is not valid JSON'),
		(
			json.dumps({**json.loads(answer_text()), 'note': 'x'}),
			'the answer has blocks the record does not take: note',
		),
	],
)
def test_an_answer_that_fails_the_check_is_shown_as_given(sources, text, problem):
	result = record.trial('DSS-1234', POSTED, ask=claude(text, []))
	assert problem in '\n'.join(result['problems'])
	assert result['dm'].startswith('**DSS-1234 · the trial answer failed the record check**')
	assert text[:100] in result['thread']


def test_a_trial_needs_an_issue_key(sources):
	with pytest.raises(ValueError, match='not an issue key'):
		record.trial('..\\DSS-1234', POSTED, ask=claude(answer_text(), []))


def test_cli_prints_the_dm_and_thread_of_a_trial(sources, monkeypatch, capsys, tmp_path):
	def run_reply(prompt, trace=None, system=''):
		assert 'Answer with one JSON object' in system
		return {'result': answer_text(), 'total_cost_usd': 0.3, 'usage': {'input_tokens': 5000}}

	monkeypatch.setattr(cli, 'load_env', lambda: None)
	monkeypatch.setattr(runner, 'run_reply', run_reply)
	monkeypatch.setattr(sys, 'argv', ['dss', 'trial', 'DSS-1234', '--posted', POSTED])
	cli.main()
	out, err = capsys.readouterr()
	assert out.startswith('**DSS-1234 · proved') and '\n--- thread reply ---\n' in out
	assert 'record check: passed' in err and str(tmp_path / 'trial') in err
	monkeypatch.setattr(sys, 'argv', ['dss', 'trial', 'DSS 1234'])
	with pytest.raises(SystemExit) as stopped:
		cli.main()
	assert stopped.value.code == 2
