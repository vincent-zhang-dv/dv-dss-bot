# Status (newest first; add an entry per work session)

### 2026-10-29: baseline of the 30 golden tickets; the golden set goes on a review page (Vincent, with Claude)
- **Changed:** `dss eval` no longer reads a host such as `8000@ACME-12` as a Jira key (one test
  added).
- **Checked:** `uv run pytest` (78 passed); mypy clean. Baseline: the 30 golden tickets triaged
  with `python -m dss triage --posted`, traces on. Each run read Cerberus as of the ticket date:
  `DV_CERB_REF` was set to the last origin/master commit before the ticket. Cost about $7 in all,
  30 to 75 s per run, no failures. `dss eval` on them: every citation resolved except one, a
  license host written like a key. 18 of 30 answers are over the 3,500-character limit. 10 of 30
  start with a status line ("I've finished the checks…") before the title, and that line would
  reach the DM too.
- **Claude's draft verdicts (Vincent to check):** 17 right, 9 partly, 4 missed. Code bugs are the
  strongest: 9 of 13 playbook tickets right. Setup problems are the weakest. Of the pilot
  tickets, DSS-1225 and DSS-1226 are now right, DSS-1228 and DSS-1229 partly, DSS-1217 still
  missed.
- **Found: the replay is not yet fair.** These need fixing before the scores mean much:
  - A closed ticket still shows the bot its status, its Investigation Outcome and its linked
    DTN titles.
  - Some descriptions were edited after the fix; one is now the answer itself.
  - `cerb_prod_version` reports today's version while the code is pinned. Four answers therefore
    wrongly say a fix "is not in prod 1.0.4".
  - `code_log_s` across all branches finds fixes made after the ticket.
  - Project folders show today's state: folders renamed since, and runs made after the fix.
  - Some Confluence pages were written after the ticket.

  Separately, `cerb_client_info` output was too large to read in 6 answers.
- **Jira:** DSS-877's Toolbox Name changed from CERB-WI to CAT, as Vincent asked; read back.
- **Outside the repo:**
  - PR #5 opened for `dss eval`.
  - The private claude.ai page "DSS Golden Set" holds the 30 tickets. Each shows what the
    ticket said at the start, the real outcome, the bot's answer, and Claude's draft key and
    verdict. Vincent checks them there.
  - `out/eval/export_golden.py` writes `golden.jsonl` from the saved checks.
  - `run_baseline.py` now pins the code per ticket and skips tickets it has already run.
- **Not done:**
  - Vincent's check of the 30.
  - A replay mode in the tools for closed tickets, which needs both owners.
  - The judge has not run live.
  - Ruff.

### 2026-10-28: answer scoring (`dss eval`) and a golden-set draft (Vincent, with Claude)
- **Changed:**
  - `python -m dss eval <golden> <run folder> [--judge]` scores saved answers against golden
    rows (`dss/eval.py`): evidence read (from the trace's call arguments), citations under
    Checked that resolve (Jira keys, Confluence page ids, code `file:line` at the commit the
    trace shows), known false leads, Suggested labels against Jira's, length. `--judge` asks
    Haiku through `claude -p` with no tools, MCP servers or settings; off by default.
  - `docs/golden.example.jsonl`: the golden row format, with made-up data.
  - `code.files` and `code.line_count`, read-only git helpers for the citation check (not
    tools).
- **Checked:** `uv run pytest` (78 passed; `tests/test_eval.py` runs on the example row with
  faked lookups); mypy clean. Live, no Claude run: `dss eval` on the saved DSS-1229 smoke
  answer parsed all six sections, resolved both Jira citations and matched both labels
  (evidence 0/2, since that run had no trace).
- **Outside the repo (client data, `out/eval/`):** Vincent reviewed the 38 high-confidence
  drafts on the review page; their labels and answers are now in Jira (27 field edits, 18
  CAUSE/EVIDENCE/SOLUTION comments, through Vincent's Atlassian login, each checked back).
  The test-set sort now has 161 ready, 27 needing information, 192 out. A 30-row golden draft
  (`golden_draft.jsonl`: 9 Gate B, 7 Gate C, 13 playbook, 1 Gate A; 5 pilot tickets the bot
  got wrong or stopped short on) and a dry-run-by-default baseline loop (`run_baseline.py`).
- **Not done:** the golden rows need Vincent's evidence pointers and false leads; no baseline
  run (the run budget is not agreed); the judge has not been run live.

### 2026-10-28: smoke runs on Opus; usage log, tool-call traces, `--post-file` (Vincent, with Claude)
- **Smoke runs** (Opus 5.5, high effort; the tracker's Phase 0 smoke tickets): DSS-1228 cost
  $0.24 (123k tokens, 40 s) and DSS-1229 $0.17 (75k tokens, 36 s). Both stopped at Gate C:
  without the workflow's post they had no error text (DSS-1228) and no run folder (DSS-1229).
- **Changed:**
  - Every triage appends a row to `out/usage.csv` (`triage.log_usage`): run time, key,
    posted, turns, seconds, each token kind, cost. The CLI's cost line shows the turns too.
  - `DSS_TRACE=1` traces a triage's tool calls to `out/trace/<key>_<run time>.jsonl`. The
    runner passes the file to `claude -p` as `DSS_TRACE_FILE`; the MCP server inherits it, and
    `server.ReportingTools` writes one JSON line per call: tool, arguments, seconds, then the
    error, or the result's size and first 2,000 characters. The file is named by run time,
    not ticket time, so a rerun keeps the earlier trace for comparison.
  - `python -m dss triage --post-file post.txt` gives Claude the workflow's post, as the Slack
    bot does.
  - The `ask` callable now takes `trace` as a keyword argument (`runner.run_reply` and the
    test fakes).
- **Checked:** `uv run pytest` (62 passed; new tests for the usage rows, the trace lines, the
  trace setting and `--post-file`); mypy clean apart from the Cerberus import it can't see.
  Live: `claude -p` with the real `dss` server called `cerb_prod_version`, and the trace file
  got its line.
- **Not done:** no full triage with `DSS_TRACE=1` or `--post-file` yet; the next DSS-1228 and
  DSS-1229 reruns with their posts would show both.

### 2026-10-28: the triage runs on Opus 5.5 at high effort (Vincent, with Claude)
- **Changed:** `runner.MODEL` is `claude-opus-5-5` (was `claude-sonnet-5-5`), and `claude -p`
  gets `--effort high` (`runner.EFFORT`). This is for now, while reply quality is measured;
  `python -m dss triage --model` still picks another model for one run.
- **Checked:** `uv run pytest` (57 passed); the runner test checks the `--effort` value. The CLI
  (2.1.292) ran Opus 5.5 at high effort on a one-word prompt with no tools.
- **Not done:** no triage on a real ticket yet. Opus at high effort costs more per run than
  Sonnet, so the run budget agreed with Annika should allow for it.

### 2026-10-27: arch dropped; `jira_*` on the Jira REST API (Vincent, with Claude)
- **Why:** arch is not part of this project. Cerberus reads only the client info and the other
  spreadsheets in the DVT folder, which the `cerb_*` tools already read through the project
  index; arch's data has to match client info, so client info is the source here. The `jira_*`
  tools used arch only for its `DVJira` client (a Jira API token in AWS Secrets Manager).
  If Cerberus starts reading arch directly one day, arch becomes a source the triage must read
  too, and a read-only arch tool comes back (both owners agree first).
- **Changed:** `dss/jira.py` calls Jira Cloud's REST API directly (`/rest/api/2/issue/<key>`
  and `/rest/api/2/search/jql`, GET only, standard library, like `confluence.py`). Settings
  `JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN`; each falls back to its `CONFLUENCE_*` value, since one
  Atlassian site and API token serve both. Errors reach Claude as `JiraError` (not set up, token
  refused, no access, no such ticket, bad JQL). `worker.APPS` is `('cerb',)`; `DSS_ARCH_*` and
  `DV_JIRA_EMAIL` are gone from `.env.example`, README and CLAUDE.md.
- **Checked:** `uv run pytest` (57 passed); the Jira tests fake `urlopen` and check the URLs,
  fields and basic-auth header. Live on Vincent's PC (2026-10-28), with a classic Atlassian API
  token in `CONFLUENCE_*`, through the MCP server as Claude calls the tools: `jira_get_issue`
  returns every triage field for a real ticket (458 characters), so the custom field ids in
  `TICKET_FIELDS` are right; `jira_search` and `confluence_search` return results.
- **Not done:** a scoped read-only token (`read:jira-work`, `search:confluence`,
  `read:page:confluence`) would make the token itself read-only; the classic one has the
  account's rights, though the tools only send GET requests. Confluence titles come back
  HTML-escaped (`&amp;`), and the DVT number as a float (`4834.0`); both are small fixes.

### 2026-10-21 to 22: watcher moved here; first commit with fixes and tests (Vincent)
- **Watcher:** Vincent's private DSS watcher runs in a Claude Code session in this repo, as a
  session schedule at minutes 7 and 37, from `out/watcher/` (git-ignored): runbook, state,
  metrics, `smoke.py` (which bot tools work on this PC) and `project_facts.py` (one ticket's
  project facts through the index). It runs `python -m dss triage` only when the smoke check
  says the bot is ready; the session finishes what the bot can't. DSS-1226 was the first new
  ticket; the session triaged it itself, because the bot has no Jira yet.
- **Setup on Vincent's PC:** `.env` has the Cerberus checkout and venv (prod 1.0.4 is
  `ff8a0091e`) and the project index login. A live read of DSS-1225's project passed every
  project tool: `find_project`, `list_folder`, `last_session`, `read_text` and `client_info`.
  Still unset: arch (no `arch-master` worktree yet, AWS access, `DV_JIRA_EMAIL`) and
  Confluence. Until then the bot stops at Record.
- **Blind test on DSS-1225:** given only the workflow's post, the bot missed the cause. The
  client info had been fixed after the ticket, it never searched the code for "2D log", and it
  called a by-design metres/feet difference a lead. Lessons under Next.
- **Fixed in the first commit:**
  - Tool errors reach Claude with their message. mcp 2.x answers any exception other than
    `ToolError` with only "Error executing tool <name>"; `server.ReportingTools` re-raises a
    tool's exceptions as `ToolError`.
  - `code_log_s` no longer times out. Git for Windows runs its `astextplain` text filter on every
    .docx and .pdf blob during `git log -S`: 59 s on Cerberus, then a failure on a committed
    Office lock file. With `--no-textconv` it takes 0.6 s at the release, 4.6 s over all remote
    branches.
  - `DV_PROJECT_INDEX_URL`, `DV_PROJECT_INDEX_ROOT` and `DV_CERB_PROD_DIR` are read at each call.
    They were read at import, before `python -m dss` loads `.env`, so `python -m dss mcp`
    ignored their `.env` values.
  - An index request that times out now says "did not answer within 60 s". It said "cannot reach
    the project index", which misled the DSS-1226 triage: other files read fine.
  - Readability: no inline conditionals or one-letter names, constants at the top of each module,
    formal one-line comments. `worker` names an unknown app instead of raising a bare `KeyError`.
- **Checked:** 55 tests pass (24 before), 98% line coverage (not covered: the Slack Socket Mode
  connection and the `__main__` guard), mypy clean with `--check-untyped-defs`, no problems in
  VS Code. The two defect fixes have tests that fail without them. ruff left to Vincent.
- **For dv-auto-setup-agent (to pass on):** its MCP server may hide tool error messages in the
  same way, if it uses mcp 2.x and its tools raise ordinary exceptions.
- **Proved on DSS-1225, for the prompt:** a missing `casingSplit` column is added empty, so there
  are no split marks (`clientinfo.py:520-535` at `ff8a0091e`). Without marks, the 2D log starts a
  new casing interval at any ID/OD change or any depth gap between joints, with no tolerance
  (`:589`). `check_casing_record` allows gaps up to 1e-4 (`:47`), so a 1.6e-12 ft gap split one
  casing size in two without a warning (DTN-3717).
- Removed a client name from the 2026-10-20 entry: client names stay out of commits.
- **Next** (tool and prompt changes, each tested on real tickets before and after):
  - `code_grep` case-insensitive ("no_analysis" missed NO_ANALYSIS).
  - `python -m dss triage --post-file`: the workflow's post, so the bot has the summary, client
    and DVT when Jira is down; the reply's turn count in the usage line.
  - File times in `cerb_list`, so the bot can tell an input changed after the ticket.
  - `dss/triage.md`: the DSS-1225 facts above; search the code for the output's name as the
    analyst writes it; a unit difference is a lead only with the code that reads it.
  - A knowledge base of past tickets (prototype in `F:\git\dss-kb`, no data) waits on the
    decision to store ticket data locally, in `out/kb/`.
  - An event-driven trigger for the watcher (`out/watcher/poll.py`) is being built in another
    session.

### 2026-10-21: split out of dv-auto-setup-agent (Vincent)
- **Why:** the triage shares nothing with the casing intake and auto-setup work in
  dv-auto-setup-agent (Arsenii's repo), and needs its own reviewers (Vincent and Annika). The DSS
  work was all uncommitted there, so it moved with no history to untangle.
- **Moved:** `dv_mcp/jira.py`, `dv_mcp/code.py`, the project-index tools of `dv_mcp/cerb.py`,
  `dv_mcp/dss_triage.md` (now `dss/triage.md`, unchanged), `tests/test_dss.py` (now
  `tests/test_tools.py`), and `SlackDesk.on_message` / `catch_up` with their tests.
- **Copied from dv-auto-setup-agent at b3f182e, cut down to triage:** `dv_mcp/claude.py` (now
  `dss/runner.py`: one prompt, no Read, no `--add-dir`), `dv_mcp/worker.py`, `dv_mcp/confluence.py`
  with its tests, `cerb.client_info`, and the Slack formatting helpers. Fixes there don't reach
  this repo on their own.
- **Changed in the move:**
  - The MCP server is `dss` (tools `mcp__dss__*`) and serves the 13 triage tools only.
  - No chat sessions: a triage is one answer, saved as `out/answers/<channel>_<ts>.json` so
    `catch_up` knows which tickets are done.
  - `python -m dss triage DSS-1234` runs a triage without Slack, for development.
  - The venv settings are required (`DSS_CERB_PY`/`_CWD`, `DSS_ARCH_PY`/`_CWD`) instead of
    defaulting to another machine's paths. The Slack variables are `DSS_SLACK_*`, so the bot
    can't pick up the DV Assistant app's tokens by accident.
  - A `.env` file at the repo root fills in unset variables.
  - Its own Slack app (`slack_manifest.yaml`) with the `message.channels` / `message.groups`
    events the DV Assistant manifest lacked.
  - `CLAUDE.md` with the rules for Claude Code sessions, shared by both owners.
- Checked: 24 tests pass, ruff clean; `python -m dss mcp` lists 13 tools over stdio.

### 2026-10-20: DSS ticket triage (Vincent; in dv-auto-setup-agent, branch `work-vincent-dss-profile`, read-only)
Paths below are this repo's.
- **Why:** Vincent's private DSS watcher (Claude Code with Slack and Jira MCP, Oct 16-20) showed
  that a fixed triage order keeps Claude on track: on the new flow it went through its sources
  once each, in order, read no screenshots and proved every claim. But it ran in a full session
  where "never post, never edit" were only instructions. The triage moved onto a locked-down
  runner, where those limits are enforced.
- **System prompt** `dss/triage.md` is the triage flow: record, Gate A (Cerberus?), Gate B (known
  setup problem?), Gate C (inputs there?), one playbook, only proved results. Tools only, no Read.
- **Read-only tools:**
  - `jira_get_issue` / `jira_search` (`dss/jira.py`): arch's `DVJira` in the arch venv (token
    from AWS Secrets Manager, account email from `DV_JIRA_EMAIL`). Only the triage fields are
    requested.
  - `cerb_find_project`, `cerb_last_session`, `cerb_prod_version` (`dss/cerb.py`): the DVT's
    project folder (through the project index, see Project access), the reporter's newest
    telemetry log before the ticket (Cerberus version, failed `script_run`s, last
    `config_saved`), and the prod wheel version.
  - `code_grep` (fixed-string by default), `code_read`, `code_log_s` (`dss/code.py`): git reads
    at the `release: v<prod version>` commit on origin/master (or `DV_CERB_REF`), never the
    working tree, so answers match what analysts run.
- **Slack:** `SlackDesk.on_message` triages each "created DSS-…" workflow post (900 s timeout)
  and sends the analysis to the reviewer's DM only, with no Post button. People's messages,
  thread replies, other channels and redelivered events are ignored.
- **Around the clock (off-hours support):** the bot runs on a PC that stays on, started at logon
  by a scheduled task (README). This replaces Vincent's 30-minute polling, which cost about 0.5k
  tokens per empty check and could leave a ticket waiting 30 minutes. Slack does not resend
  missed events, so on start `catch_up` triages tickets from the last 24 h with no saved answer,
  oldest first, alongside live events. Failed triages are retried there too.
- **Project access (2026-10-21):** Z: opens a project only after it is requested in VizApp, for
  45 days, so the bot's login could read 19 of 357 client folders. The `cerb_*` project tools
  read through the project index (Everything HTTP server at 192.168.25.120 over
  `\\192.168.24.252\projects`), approved for read-only use on DSS tickets. They send only GET
  requests, and only for paths inside that share. The login is `DV_PROJECT_INDEX_USER` and
  `DV_PROJECT_INDEX_PASSWORD`. Tools: `cerb_list` (a folder's direct entries, for Gate C inputs)
  and `cerb_read_text` (400-line pages, for the config named by `config_saved`).
  `cerb_client_info` copies a share file to the temp folder first. Checked live on Vincent's PC:
  `find_project` returns a real client project and `list_folder` (`parent:`) lists its
  direct entries; a telemetry log started at 13:02 the same day was already indexed, and
  `read_text` and `last_session` read it (Cerberus 1.0.4); `local_copy` + `client_info` read
  the project's client info.
- **Not done:**
  - Not run against real Jira or Slack. That needs `DV_JIRA_EMAIL` plus arch's AWS access, and
    the bot tokens.
  - Checked live (read-only) on Vincent's PC: prod version 1.0.4 maps to `ff8a0091e`;
    `code_grep`/`code_read` return the expected lines.
- **Next:**
  - Pilot alongside the watcher and compare drafts and tokens per ticket.
  - Then decide whether the reviewer gets a Post button for ticket triage.
