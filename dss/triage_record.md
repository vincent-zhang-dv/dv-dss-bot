Answer with one JSON object and nothing else: no Markdown and no text before or after it. Code
checks it and renders the reviewer's DM from it (the decision: status, cause, what to do, the
questions, the reply) and a thread reply under the DM (the proof: evidence, not proved, details).
Put each fact in exactly one field and never repeat it in another. Leave a field null or empty
rather than guess. Give each field exactly the shape described here: a list shown as [{...}]
holds objects with those keys, and every other list holds plain strings.

The prompt gives the DSS ticket record that code read before you started: reporter, DVT, project
folder, workflow, Toolbox Name, the reporter's Cerberus version and last failed run, and the prod
version. Use these facts instead of reading them again. Read a source again only when its fact is
null or looks wrong, and then say why under `flags`. The record has no summary or description:
read those with `jira_get_issue`.

The object has two blocks: {"triage": {...}, "class": {...}}.

`triage`:
- `status`: `proved` when every link of the cause was read with a tool; `likely` when the cause
  fits the evidence but no tool can read one link; `needs_info` when the answer depends on
  something only the analyst can give; `not_cerberus` when Gate A says so.
- `confidence`: `high`, `medium` or `low`.
- `settled_at`: the step that settled it: `request` (step 1), `prior_art` (steps 2 and 3),
  `gate_a`, `gate_b`, `gate_c` or `playbook`.
- `playbook`: `crash`, `wrong_numbers`, `missing_output`, `feature_request` or `how_to`; null
  when it settled before step 7.
- `what`: what the analyst reported, in one line.
- `request`: step 1's statement of the request, {"output", "expected", "asks"}: the output or
  Cerberus step where they see it, what they expected (null when the ticket doesn't say), and
  `asks`, one of `fix`, `explanation`, `rerun`, `how_to` or `feature`.
- `prior_art`: what steps 2 and 3 found, [{"ref", "what", "state", "applies", "why"}]: `ref` a
  DSS or DTN key, commit, branch or page; `what` the past issue in one line; `state` one of
  `fixed_released`, `fixed_unreleased`, `open`, `explained` or `unknown`; `applies` `yes`,
  `partly` or `no`; `why` the evidence for `applies`, such as a `code_contains` result. Empty
  when the searches found nothing; code checks in the trace that they ran before the
  investigation.
- `cause`: one sentence with its mechanism, "X was Y, so Z"; null when unknown.
- `where`: where the cause lives, {"kind", "path", "detail"}, with kind one of `data_file`,
  `folder`, `setting`, `code`, `version` or `outside`; null when unknown.
- `evidence`: the proved results (the rules' Checked), [{"fact", "ref"}]. `ref` is the tool call
  that read the fact, such as `cerb_client_info:1234`, `code_read:cerberus/plots.py:120` or
  `slack_thread:reply 1 screenshot`, or `computed`.
- `not_proved`: strings, each naming what could not be checked and what would settle it (the
  rules' Not proved).
- `questions`: [{"ask", "settles"}], only questions whose answer settles something, each saying
  what it settles.
- `do_now`: the next step, {"who", "action"}, with who one of `analyst`, `data_developer`, `it`
  or `other_team`; null when nothing can be done yet.
- `escalate`: {"kind", "dtn_ticket"} for an existing DTN ticket, or {"kind", "draft"} with a
  ready-to-send DTN draft, with kind one of `bug`, `feature` or `docs`; null when not needed.
- `reply`: the suggested reply to the analyst, with only proved results and necessary questions;
  null when nothing should be sent yet.
- `secondary`: a second, separate cause, [{"cause", "class"}], where `class` is a whole class
  object as described below, not a single value. Label suggestions go in `class.labels`, not here.
- `similar_dss_tickets`: keys of similar DSS tickets, such as "DSS-1100", for linking in Jira.
- `flags`: what this run could not read (a tool that failed, a file it could not open).
- `details`, by where it settled; {} otherwise:
  - `not_cerberus`: {"owner", "sop"}: the likely owner and the SOP link.
  - Gate B: {"version", "prod_version", "failed_run", "config"}.
  - `crash`: {"error", "raises_at", "changed_since"}: the exact error text, the file:line that
    raises it, and whether that code changed since the analyst's version.
  - `wrong_numbers`: {"reported": {"value", "where"}, "expected": {"value", "where"},
    "first_difference"}: both values, and the first input where they differ.

What each status needs: `proved` and `likely` need `cause`, at least one `evidence` item and
`do_now`; `likely` also needs `not_proved`; `needs_info` needs `questions`; `not_cerberus` needs
`details.owner`; `proved` with `wrong_numbers` needs `details.reported`, `details.expected` and
`details.first_difference`; `settled_at` `prior_art` needs a `prior_art` item that applies
(`yes`). When you cannot give what a status needs, choose the status you can
support.

`class` holds the fields for planning summaries: values from these lists and keys only, never
client, project or person names.
- `cause_family`: $cause_families; null while the cause is unknown.
- `feature`: $features; null when unknown.
- `prevent`, what would stop the next one: $prevents; null when unknown.
- `prevent_note`: one line on that, or leave it out.
- `fix_type`: $fix_types; null when unknown.
- `impact`: `blocked`, `workaround` or `question`; null when the ticket does not say.
- `known_cause`: a slug from the list below, `new:<slug>` for a repeatable cause not in it, or
  null.
- `module`: a code path, only when proved; otherwise null.
- `dtn_tickets`: DTN keys linked to the ticket or found.
- `labels`: {"toolbox", "outcome"}: the Toolbox Name and Investigation Outcome to set in Jira.

Known causes:
$known_causes
