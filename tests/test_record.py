"""Trial mode: the record code reads, the prompt, the check, the DM and thread, and the CLI."""

import json
import sys
from pathlib import Path

import jsonschema  # type: ignore[import-untyped]
import pytest

from dss import __main__ as cli
from dss import cerb, code, jira, record, runner, triage

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
IN_ORDER = ('jira_get_issue', 'jira_search', 'code_log_s', 'code_read', 'cerb_client_info')
NO_TERMINAL_LOG = {'found': False, 'why': 'Cerberus 1.0.3 writes no terminal log; 1.1 and later do'}


def answer_text(**changes) -> str:
	"""Claude's answer: the example's triage and class blocks, with *changes* to triage."""
	return json.dumps({'triage': {**EXAMPLE['triage'], **changes}, 'class': EXAMPLE['class']})


def write_trace(trace, tools) -> None:
	"""The trace a run that called *tools* in order leaves, as the `dss` server writes it."""
	if trace is not None:
		lines = [json.dumps({'tool': tool, 'arguments': {}, 'seconds': 0.1}) for tool in tools]
		trace.write_text(''.join(line + '\n' for line in lines), encoding='utf-8')


def claude(text: str, prompts: list, tools=IN_ORDER):
	"""A fake Claude that calls *tools*, answers *text* and keeps the prompts it gets in
	*prompts*."""

	def ask(prompt, trace=None):
		prompts.append(prompt)
		write_trace(trace, tools)
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
		session = {'found': True, 'cerb': '1.0.3', 'failed_runs': [failed], 'last_config': ''}
		return {**session, 'terminal_log': NO_TERMINAL_LOG}

	monkeypatch.setattr(jira, 'get_issue', get_issue)
	monkeypatch.setattr(cerb, 'find_project', find_project)
	monkeypatch.setattr(cerb, 'last_session', last_session)
	monkeypatch.setattr(cerb, 'prod_version', lambda: '1.0.4')
	monkeypatch.setattr(code, 'web_links', lambda text: {})
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
		'terminal_log': NO_TERMINAL_LOG,
	}
	assert notes == []  # no terminal log is expected before Cerberus 1.1
	assert ('jira', 'DSS-1234', True) in sources  # with the created time
	assert ('telemetry', PROJECT, 'Ann Analyst', POSTED) in sources  # the session before the post


def test_a_1_1_sessions_terminal_log_goes_to_claude_and_a_missing_one_is_noted(
	sources, monkeypatch
):
	log = {
		'found': True,
		'path': PROJECT + r'\cerberus\terminal_log\AnalystAnn_terminal_log_x.log',
		'errors': ['line 17: ERROR cat_view:run_analysis_on_click:2538 - CATCALL run failed'],
		'warnings': [],
	}
	session = {'found': True, 'cerb': '1.1.0.68', 'failed_runs': [], 'last_config': ''}
	monkeypatch.setattr(cerb, 'last_session', lambda *args: {**session, 'terminal_log': log})
	ticket, notes = record.build_ticket('DSS-1234', POSTED)
	assert ticket['terminal_log'] == log and notes == []
	assert 'line 17: ERROR cat_view:run_analysis_on_click:2538' in record.prompt(
		'DSS-1234', POSTED, ticket, notes
	)
	full = {'schema': record.SCHEMA_ID, 'dss_ticket': ticket, **json.loads(answer_text())}
	assert record.check(full) == []
	missing = {'found': False, 'why': 'terminal log not read: no such file in the project index'}
	monkeypatch.setattr(cerb, 'last_session', lambda *args: {**session, 'terminal_log': missing})
	assert record.build_ticket('DSS-1234', POSTED)[1] == [missing['why']]


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
	assert 'Answer with the structured output' in prompt
	assert '\nAnswer in Markdown' not in prompt
	for cause in json.loads(record.KNOWN_CAUSES_FILE.read_text(encoding='utf-8')):
		assert f'`{cause["slug"]}`' in prompt  # an unknown workflow gets every known cause
	assert '$' not in prompt


def test_the_answer_schema_is_the_records_own_blocks_standing_alone():
	schema = record.answer_schema()
	assert set(schema['properties']) == {'triage', 'class'} == set(schema['required'])
	assert '$ref' not in json.dumps(schema)  # claude gets one schema without references
	assert 'allOf' not in schema  # the API takes no allOf, anyOf or oneOf at the top level
	fields = schema['properties']['triage']['properties']
	assert fields['headline']['maxLength'] == 60 and fields['headline']['type'] == 'string'
	assert 'as a data developer would write it' in fields['reply']['description']
	replay = record.answer_schema(replay=True)['properties']['replay']
	assert set(replay['properties']) == {'fixed_later', 'leaks'}  # code fills the rest
	# It goes on the claude command line, which Windows caps at 32,767 characters
	assert len(json.dumps(record.answer_schema(replay=True), separators=(',', ':'))) < 24_000


def test_an_answer_that_fits_the_schema_makes_a_record_that_passes(sources):
	answer = json.loads(answer_text())
	jsonschema.validate(answer, record.answer_schema())
	ticket, _notes = record.build_ticket('DSS-1234', POSTED)
	_record, problems = record.assemble(ticket, answer_text())
	assert problems == []


@pytest.mark.parametrize(
	('workflow', 'kept', 'left_out'),
	[
		('PE', 'pe_baselines', 'wi_las_export'),
		('WI', 'wi_las_export', 'pe_baselines'),
		('CI', 'wi_las_export', 'ff_report'),
	],
)
def test_a_ticket_gets_the_known_causes_of_its_own_workflow(workflow, kept, left_out):
	assert record.for_workflow({'feature': kept}, workflow)
	assert not record.for_workflow({'feature': left_out}, workflow)
	assert record.for_workflow({'feature': 'client_info'}, workflow)  # any workflow's
	assert record.for_workflow({'feature': None}, workflow)
	assert record.for_workflow({'feature': left_out}, 'unknown')
	assert record.for_workflow({'feature': left_out}, 'combo')


def test_a_broken_known_causes_file_is_named_not_blamed_on_claude(tmp_path, monkeypatch):
	broken = tmp_path / 'known_causes.json'
	broken.write_text("mo'r[\n\t{}\n]\n", encoding='utf-8')  # keys typed into the open file
	monkeypatch.setattr(record, 'KNOWN_CAUSES_FILE', broken)
	with pytest.raises(ValueError, match=r'known_causes\.json is not valid JSON') as caught:
		record.system_prompt()
	assert triage.explain(caught.value).startswith('The triage failed: known_causes.json is not')


def test_the_trial_runs_the_same_locked_down_claude(sources, monkeypatch, tmp_path):
	asked = []

	def run_reply(prompt, trace=None, system='', schema=None, budget=0):
		asked.append({'system': system, 'schema': schema, 'budget': budget})
		return {'result': answer_text(), 'total_cost_usd': 0.3, 'usage': {}}

	monkeypatch.setattr(runner, 'run_reply', run_reply)
	record.trial('DSS-1234', POSTED)
	(call,) = asked
	assert call['system'] == record.system_prompt('WI')  # the fake project folder is a WI job
	assert call['schema'] == record.answer_schema() and call['budget'] == 0
	line = runner.command(system_file=tmp_path / 'system.md', schema=call['schema'])
	bot = runner.command()
	prompt = line.index('--system-prompt-file') + 1
	assert line[:prompt] == bot[:prompt]
	assert line[prompt + 1 :] == [*bot[prompt + 1 :], '--json-schema', line[-1]]
	assert json.loads(line[-1]) == call['schema']


def test_a_trial_checks_and_saves_the_record(sources, tmp_path):
	prompts: list = []
	post = 'created DSS-1234 · ACME · DVT1234'
	ask = claude(answer_text(), prompts)
	result = record.trial('DSS-1234', POSTED, post, ask=ask, thread_ts='1792523850.5')
	assert result['problems'] == []
	assert result['record']['dss_ticket'] == result['ticket']
	assert result['record']['triage'] == EXAMPLE['triage']
	assert result['record']['class'] == EXAMPLE['class']
	assert result['thread_ts'] == '1792523850.5'
	(prompt,) = prompts
	assert prompt.startswith(f'New ticket DSS-1234, posted {POSTED} (local time).')
	assert json.dumps(result['ticket'], indent=1) in prompt
	assert '`slack_thread("1792523850.5")`' in prompt
	assert prompt.endswith('workflow posted:\n' + post)
	saved = Path(result['file'])
	assert saved.parent == tmp_path / 'trial'  # never the bot's out/answers
	assert json.loads(saved.read_text(encoding='utf-8')) == result
	assert Path(result['trace']).parent == tmp_path / 'trace'  # a trial always keeps its trace


@pytest.mark.parametrize(
	('tools', 'status', 'problems'),
	[
		(IN_ORDER, 'proved', []),
		(
			('jira_get_issue', 'code_read', 'jira_search', 'code_log_s'),
			'proved',
			['jira_search', 'git history'],  # both came after the investigation started
		),
		(('jira_search', 'code_grep', 'cerb_read_sheet', 'code_log'), 'proved', ['git history']),
		(('jira_search', 'code_branches', 'cerb_read_text'), 'proved', []),
		(('jira_get_issue', 'jira_search'), 'not_cerberus', []),  # no git search needed
		((), 'proved', ['jira_search', 'git history']),  # no tool calls at all
	],
)
def test_past_issues_are_searched_before_the_investigation(tools, status, problems):
	found = record.order_problems({'triage': {'status': status}}, list(tools))
	assert len(found) == len(problems)
	for problem, word in zip(found, problems, strict=True):
		assert problem.startswith('trace: ') and word in problem


def test_a_trial_notes_a_cerberus_refresh_that_failed(sources, monkeypatch):
	note = 'prod Cerberus not unpacked, so cerb_client_info may fail: no wheel'
	monkeypatch.setattr(code, 'refresh', lambda: [note])
	prompts: list = []
	result = record.trial('DSS-1234', POSTED, ask=claude(answer_text(), prompts))
	assert result['notes'] == [note]
	assert f'Code could not read:\n- {note}' in prompts[0]
	assert 'could not read:\n- prod Cerberus not unpacked' in result['thread']  # for the reviewer


def test_a_malformed_block_is_a_problem_not_a_crash(sources):
	text = json.dumps({'triage': 'proved, see above', 'class': EXAMPLE['class']})
	result = record.trial('DSS-1234', POSTED, ask=claude(text, []))
	assert any(problem.startswith('triage:') for problem in result['problems'])
	assert 'crash' not in result
	assert record.order_problems({'triage': 'x'}, list(IN_ORDER)) == []


def test_a_rendering_bug_never_loses_the_answer(sources, monkeypatch):
	def broken(*args):
		raise AttributeError("'str' object has no attribute 'get'")

	monkeypatch.setattr(record, 'render', broken)
	result = record.trial('DSS-1234', POSTED, ask=claude(answer_text(), []))
	(problem,) = result['problems']
	assert problem.startswith('the answer could not be checked or rendered: AttributeError')
	assert 'Traceback' in result['crash']
	assert result['record']['triage'] == EXAMPLE['triage']  # checked before the crash, so kept
	assert result['dm'].startswith('**DSS-1234 · the trial answer failed the record check**')
	assert answer_text()[:100] in result['thread']  # the paid answer is shown and saved
	assert json.loads(Path(result['file']).read_text(encoding='utf-8'))['text'] == answer_text()


def test_a_trial_that_skips_past_issues_fails_the_check(sources):
	text = answer_text()
	result = record.trial('DSS-1234', POSTED, ask=claude(text, [], tools=('code_read',)))
	assert any('no jira_search' in problem for problem in result['problems'])
	assert result['dm'].startswith('**DSS-1234 · the trial answer failed the record check**')


def test_the_post_decides_and_the_thread_proves_without_repeats(sources):
	result = record.trial('DSS-1234', POSTED, ask=claude(answer_text(), []))
	dm, thread = result['dm'], result['thread']
	answer = EXAMPLE['triage']
	assert dm.startswith(
		'**DSS-1234 · Input problem: casing ID differs from the tally · proved**\n'
		f'**Bot Summary:** {answer["what"]}\n'
		'**Ann Analyst Todos:**\n'  # the Jira reporter, with no Slack name given
		'1. Set the ID in DVT1234\\_client\\_info.xlsm, Casing Record row 3, to 4.55 in '
		'and rerun the Defect Summary\n'
		'\n'  # a blank line ends the list, or the next title joins its last item
		f'**Draft Reply:**\n> {answer["reply"]}\n\n_Trial'  # the reply comes last, then the footer
	)
	assert 'Data Dev Todos' not in dm  # nothing for them to do
	for text in (answer['what'], answer['reply']):
		assert dm.count(text) == 1 and text not in thread
	for text in [answer['cause']] + [item['fact'] for item in answer['evidence']]:
		assert thread.count(text) == 1 and text not in dm
	assert thread.startswith(
		f'**Why:** {answer["cause"]} (data file `DVT1234_client_info.xlsm`, Casing Record row 3)\n'
		'**Past issues**\n1. DSS-1100 (explained, applies: partly): Wall'
	)
	assert 'differed from the tally. Same known cause' in thread  # what, then why
	# a blank line ends each list, or the next heading joins its last item and numbers run on
	assert 'on its own\n\n**Evidence**\n1. Casing Record row 3' in thread
	assert '- reported: 19.97% (Defect Summary, joint 57)' in thread
	assert '- first difference: casing ID' in thread
	assert thread.endswith('\n**Confidence:** high, settled at the wrong numbers playbook')
	# the class, labels and the facts code read stay in the saved record
	for label in ('Class', 'Labels', 'Facts read by code', 'wi\\_wall\\_loss', 'KeyError'):
		assert label not in thread
	assert dm.endswith('\n\n_Trial · draft by Claude, for you only · 5,000 tokens · ≈ $0.30 · 0 s_')


def test_questions_and_what_is_not_proved_become_actions(sources):
	check = 'Read the RAID run config to confirm which DVCalipers it used'
	text = answer_text(
		status='likely',
		actions={'analyst': [], 'developers': ['Raise a DTN ticket for the header overlap']},
		not_proved=[check],
		questions=[QUESTION],
	)
	result = record.trial('DSS-1234', POSTED, ask=claude(text, []))
	assert result['problems'] == []
	dm, thread = result['dm'], result['thread']
	assert f'**Ann Analyst Todos:**\n1. Answer: {QUESTION["ask"]}\n\n' in dm
	assert (
		'**Data Dev Todos:**\n1. Raise a DTN ticket for the header overlap\n'
		f'2. Confirm: {check}\n\n**Draft Reply:**'
	) in dm
	assert check not in thread  # said once, as an action
	assert f'- {QUESTION["ask"]}: settles {QUESTION["settles"]}' in thread


def test_a_check_that_starts_with_confirm_says_it_once(sources):
	text = answer_text(status='likely', not_proved=['Confirm the wheel was built from ab952f341'])
	dm = record.trial('DSS-1234', POSTED, ask=claude(text, []))['dm']
	assert '**Data Dev Todos:**\n1. Confirm: the wheel was built from ab952f341\n' in dm


def test_tickets_and_cerberus_changes_are_linked_below_the_headline(sources, monkeypatch):
	monkeypatch.setenv('JIRA_URL', 'https://jira.example')
	github = {
		'DTN-1234-work-fix': 'https://github.example/pull/7',  # the branch's PR
		'ab952f341': 'https://github.example/commit/ab952f3410',
		'Cerberus 1.1': 'https://github.example/pull/978',
	}
	monkeypatch.setattr(code, 'web_links', lambda text: github)
	text = answer_text(
		headline='Known issue DTN-1234',
		actions={
			'analyst': [],
			'developers': ['Link DSS-1234 to DTN-1234; the fix DTN-1234-work-fix is ab952f341'],
		},
		cause='See `DTN-1234` and DSS-1100.',
		reply='It is fixed in Cerberus 1.1 (DTN-1234).',
	)
	result = record.trial('DSS-1234', POSTED, ask=claude(text, []), ticket_link='https://slack/p1')
	dm, thread = result['dm'], result['thread']
	assert dm.startswith('**[DSS-1234](https://slack/p1) · Known issue DTN-1234 · proved**\n')
	assert (
		'1. Link [DSS-1234](https://jira.example/browse/DSS-1234) to '
		'[DTN-1234](https://jira.example/browse/DTN-1234); the fix '
		'[DTN-1234-work-fix](https://github.example/pull/7) is '
		'[ab952f341](https://github.example/commit/ab952f3410)\n'
	) in dm
	# analysts cannot open GitHub, so their reply links only tickets
	assert '> It is fixed in Cerberus 1.1 ([DTN-1234](https://jira.example/browse/DTN-1234)).' in dm
	assert 'See `DTN-1234` and [DSS-1100](https://jira.example/browse/DSS-1100).' in thread
	assert 'GitHub links not added' not in thread


def test_a_failed_github_lookup_still_links_tickets(sources, monkeypatch):
	monkeypatch.setenv('JIRA_URL', 'https://jira.example')

	def web_links(text):
		raise RuntimeError('git rev-parse failed: not a git repository')

	monkeypatch.setattr(code, 'web_links', web_links)
	result = record.trial('DSS-1234', POSTED, ask=claude(answer_text(), []))
	assert result['problems'] == []
	assert '[DSS-1100](https://jira.example/browse/DSS-1100)' in result['thread']
	assert '- GitHub links not added: git rev-parse failed' in result['thread']


def test_claude_text_shows_as_written(sources):
	cause = (
		r'The page path <project>\cerberus\<scene>\x.pdf is too long, so __init__ fails at `a.py:9`'
	)
	text = answer_text(cause=cause, reply='Rerun it.\nThanks')
	result = record.trial('DSS-1234', POSTED, ask=claude(text, []))
	thread = result['thread']
	assert r'\<project\>\\cerberus\\\<scene\>\\x.pdf' in thread  # Markdown would eat \< and <...>
	assert r'so \_\_init\_\_ fails at `a.py:9`' in thread  # code spans stay as they are
	assert '> Rerun it.\n> Thanks\n\n_Trial' in result['dm']  # the footer is not quoted with it


def test_the_dtn_ticket_stays_in_the_record_and_the_reply_ends_the_post(sources):
	escalate = {'kind': 'bug', 'dtn_ticket': 'DTN-3000'}
	text = answer_text(escalate=escalate, reply='Rerun it.')
	result = record.trial('DSS-1234', POSTED, ask=claude(text, []))
	assert result['record']['triage']['escalate'] == escalate
	assert '**Draft Reply:**\n> Rerun it.\n\n_Trial' in result['dm']
	assert 'DTN-3000' not in result['dm'] and 'DTN-3000' not in result['thread']


def test_also_found_shows_the_cause_and_what_was_unread_is_said_once(sources, monkeypatch):
	note = 'no Cerberus telemetry log of Ann Analyst before the post'
	monkeypatch.setattr(code, 'refresh', lambda: [note])
	second = {'cause': 'The calipers sit in a subfolder', 'class': EXAMPLE['class']}
	text = answer_text(secondary=[second], flags=['the screenshot', note])
	thread = record.trial('DSS-1234', POSTED, ask=claude(text, []))['thread']
	assert '**Also found**\n- The calipers sit in a subfolder\n\n**Confidence:**' in thread
	assert 'wi\\_wall\\_loss' not in thread  # its class stays in the record
	assert thread.endswith(
		'**Confidence:** high, settled at the wrong numbers playbook; could not read:\n'
		f'- the screenshot\n- {note}'
	)


def test_empty_fields_are_left_out(sources):
	text = answer_text(
		status='needs_info',
		confidence='low',
		settled_at='gate_c',
		playbook=None,
		cause=None,
		where=None,
		evidence=[],
		headline='One question for the analyst',
		actions={'analyst': [], 'developers': []},
		reply=None,
		details={},
		questions=[QUESTION],
	)
	result = record.trial('DSS-1234', POSTED, ask=claude(text, []))
	assert result['problems'] == []
	dm, thread = result['dm'], result['thread']
	assert dm.startswith('**DSS-1234 · One question for the analyst · needs info**\n')
	assert f'**Ann Analyst Todos:**\n1. Answer: {QUESTION["ask"]}\n\n_Trial' in dm
	assert 'Data Dev Todos' not in dm and 'Reply' not in dm
	for label in ('Why:', 'Evidence', 'Details', 'DTN', 'Also found', 'could not read'):
		assert label not in thread
	assert thread.endswith('**Confidence:** low, settled at Gate C')


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
	def run_reply(prompt, trace=None, system='', schema=None, budget=0):
		assert 'Answer with the structured output' in system and schema is not None
		write_trace(trace, IN_ORDER)
		return {'result': answer_text(), 'total_cost_usd': 0.3, 'usage': {'input_tokens': 5000}}

	monkeypatch.setattr(cli, 'load_env', lambda: None)
	monkeypatch.setattr(runner, 'run_reply', run_reply)
	monkeypatch.setattr(sys, 'argv', ['dss', 'trial', 'DSS-1234', '--posted', POSTED])
	cli.main()
	out, err = capsys.readouterr()
	assert out.startswith('**DSS-1234 · Input problem') and '\n--- thread reply ---\n' in out
	assert 'record check: passed' in err and str(tmp_path / 'trial') in err
	monkeypatch.setattr(sys, 'argv', ['dss', 'trial', 'DSS 1234'])
	with pytest.raises(SystemExit) as stopped:
		cli.main()
	assert stopped.value.code == 2
