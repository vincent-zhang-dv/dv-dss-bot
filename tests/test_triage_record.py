"""The proposed triage record: its schema, the made-up example, and the known-cause list."""

import copy
import json
import re

import jsonschema  # type: ignore[import-untyped]
import pytest

from dss.worker import ROOT

SCHEMA = json.loads((ROOT / 'docs' / 'triage_record.schema.json').read_text(encoding='utf-8'))
EXAMPLE = json.loads((ROOT / 'docs' / 'triage_record.example.json').read_text(encoding='utf-8'))
CAUSES = json.loads((ROOT / 'docs' / 'known_causes.json').read_text(encoding='utf-8'))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def errors(record):
	"""Every message the schema gives for *record*."""
	return [error.message for error in VALIDATOR.iter_errors(record)]


def with_value(section, field, value):
	"""A copy of the example with one field of *section* set to *value*."""
	record = copy.deepcopy(EXAMPLE)
	record[section][field] = value
	return record


def test_schema_and_example_are_valid():
	jsonschema.Draft202012Validator.check_schema(SCHEMA)
	assert errors(EXAMPLE) == []


@pytest.mark.parametrize(
	('section', 'field', 'value'),
	[
		('triage', 'evidence', []),
		('triage', 'do_now', None),
		('triage', 'cause', None),
		('triage', 'status', 'likely'),  # nothing under not_proved
		('triage', 'status', 'needs_info'),  # no questions
		('triage', 'status', 'not_cerberus'),  # no owner
		('triage', 'details', {'playbook': 'wrong_numbers'}),  # proved, but no trace
	],
)
def test_status_sets_what_is_required(section, field, value):
	assert errors(with_value(section, field, value))


def test_needs_info_with_a_question_needs_no_cause():
	record = copy.deepcopy(EXAMPLE)
	record['triage'].update(
		status='needs_info',
		cause=None,
		evidence=[],
		do_now=None,
		details={},
		questions=[
			{
				'ask': 'Which DVCalipers did the RAID run use?',
				'settles': 'whether they match the catcall',
			}
		],
	)
	assert errors(record) == []


@pytest.mark.parametrize(
	('section', 'field', 'value'),
	[
		('class', 'feature', 'raid_2d_log'),  # from the first list, since revised
		('class', 'known_cause', 'Casing Record'),
		('class', 'dtn_tickets', ['DSS-1100']),
		('triage', 'similar_dss_tickets', ['DTN-3000']),
		('triage', 'escalate', {'kind': 'bug'}),  # neither a DTN ticket nor a draft
	],
)
def test_values_outside_the_lists_are_refused(section, field, value):
	assert errors(with_value(section, field, value))


def test_blocks_are_named_by_project():
	record = copy.deepcopy(EXAMPLE)
	record['ticket'] = record.pop('dss_ticket')
	assert errors(record)


@pytest.mark.parametrize(
	'escalate',
	[{'kind': 'bug', 'dtn_ticket': 'DTN-3000'}, {'kind': 'docs', 'draft': 'Add a note'}],
)
def test_escalate_takes_a_dtn_ticket_or_a_draft(escalate):
	assert errors(with_value('triage', 'escalate', escalate)) == []


def test_known_causes_fit_the_schema():
	defs = SCHEMA['$defs']
	slug_pattern = defs['class']['properties']['known_cause']['pattern']
	slugs = [cause['slug'] for cause in CAUSES]
	assert len(slugs) == len(set(slugs))
	for cause in CAUSES:
		assert re.fullmatch(slug_pattern, cause['slug'])
		assert cause['cause_family'] in defs['cause_family']['enum']
		assert cause['feature'] is None or cause['feature'] in defs['feature']['enum']
		assert cause['prevent'] in defs['prevent']['enum']
		assert cause['cause'] and cause['prove'] and cause['fix'] and cause['dss_tickets']
		for key in cause['dss_tickets']:
			assert re.fullmatch(r'DSS-\d+', key)
		for key in cause['dtn_tickets']:
			assert re.fullmatch(r'DTN-\d+', key)


def test_example_known_cause_is_in_the_list():
	assert EXAMPLE['class']['known_cause'] in {cause['slug'] for cause in CAUSES}
