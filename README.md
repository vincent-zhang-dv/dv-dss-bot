# dv-dss-bot

Triages new DSS (Data Support) tickets. When a support-request workflow posts "created DSS-…" in #data-dev-support, a locked-down, read-only Claude works through the ticket and sends its analysis to a data developer's Slack DM. Nothing is posted in the channel and nothing is written anywhere.

> **Principle:** Claude reads and proves; a data developer decides what reaches the analyst.

Owners: Vincent Zhang and Annika. Status log: [docs/STATUS.md](docs/STATUS.md).

## How it works

```
#data-dev-support: a workflow posts "created DSS-1234"
  -> dss/slack.py    new ticket, triaged 5 minutes after the post so its thread holds the
                     screenshots (on start, also any from the last 24 h with no saved answer)
  -> dss/triage.py   the prompt: ticket key, time posted, the workflow's message, its thread
  -> dss/runner.py   claude -p with no user setup and no built-in tools; system prompt dss/triage.md
  -> dss/server.py   MCP server `dss`, read-only tools:
       slack_thread  dss/thread.py      the ticket's thread: replies, screenshots (Slack API)
       jira_*        dss/jira.py        ticket fields, similar tickets (Jira REST API)
       cerb_*        dss/cerb.py        project folder, telemetry, files (project index);
                                        client info (Cerberus venv)
       code_*        dss/code.py        Cerberus source at the prod release commit (git)
       confluence_*  dss/confluence.py  SOPs and how-tos
  -> the reviewer's DM; the answer is saved in out/answers/
```

The triage flow is the system prompt, [dss/triage.md](dss/triage.md). Claude first states the request, then searches past issues: DSS and DTN tickets, git history and branches, and troubleshooting pages. It then checks whether a past fix applies to this analyst's build. Only after that does it investigate. It goes through Gate A (is it a Cerberus issue?), Gate B (is it a known setup problem?) and Gate C (are the inputs there?), then follows one playbook for the symptom. Only proved results go under "Checked"; everything else goes under "Not proved", with what would settle it. Trial mode enforces the order: the record must state the request and the past issues, and the run's trace must show the Jira and git searches before any code or project file is read.

| Tool | Reads |
|---|---|
| `slack_thread` | The ticket's Slack thread: the workflow's post and every reply, oldest first, with screenshots as images and text files as text. Only threads of "created DSS-…" posts in the watched channel. |
| `jira_get_issue`, `jira_search` | A ticket's triage fields, with its resolution, fix versions (released or not), newest comments and attachment names; and similar tickets with their resolution and fix versions. |
| `cerb_find_project`, `cerb_list`, `cerb_read_text` | The DVT's project folder, a folder's entries, and a text file in 400-line pages, through the project index. |
| `cerb_last_session` | The reporter's newest Cerberus telemetry log before the ticket: version, failed script runs, last saved config. |
| `cerb_client_info` | A `client_info.xlsm` as Cerberus reads it. |
| `cerb_read_sheet` | Any `.xlsx` or `.xlsm` on the share (statistics spreadsheets, other client info sheets): its sheets and sizes, then 100-row pages with Excel's row numbers, optionally only the rows containing a text. Copied through the index and read with openpyxl in the Cerberus venv. |
| `cerb_read_document` | The text of a `.pdf` or `.docx` on the share (deliverable reports, intro pages): up to 20 pages per answer (Word in parts of about 3,000 characters), optionally only the pages containing a text. Pages that are only a picture show no text. Read with pypdf and python-docx in the Cerberus venv. |
| `cerb_read_image` | An image on the share (`.png`, `.jpg` and other bitmaps), or the pictures in one page of a PDF on the share (plots `cerb_read_document` can't read), given to Claude as images: scaled to 1568 pixels on the long side, logos left out, at most 6 per page. Read with Pillow and pypdf in the Cerberus venv. |
| `cerb_prod_version` | The version of the prod Cerberus wheel. |
| `code_grep`, `code_read`, `code_log_s` | Cerberus source through git at the prod release commit, never the working tree. |
| `code_log`, `code_branches`, `code_contains` | Cerberus history: recent commits on a file or folder, branches named with a DTN key or word, and whether a build (prod, a release or the analyst's UAT build) includes a commit. Git reads only. |
| `confluence_search`, `confluence_page` | Confluence pages. |

`cerb_client_info` and `cerb_read_sheet` run in the Cerberus venv through [dss/worker.py](dss/worker.py), so this repo needs none of its dependencies.

## Repository layout

| Path | What it does |
|---|---|
| `dss/__main__.py` | CLI: `triage`, `eval`, `slack`, `mcp`. Reads `.env`. |
| `dss/triage.md` | The triage system prompt. Most behaviour changes happen here. |
| `dss/triage.py` | One triage: prompt, Claude run, tokens and cost. |
| `dss/eval.py`, `docs/golden.example.jsonl` | Scores saved answers against the golden set; a made-up golden row. |
| `docs/triage_record.schema.json`, `docs/triage_record.example.json`, `docs/known_causes.json` | The triage record's schema (a proposal), a made-up example, and a draft list of known causes. Only trial mode uses them. |
| `dss/record.py`, `dss/triage_record.md` | Trial mode: the triage as a checked record, and the answer section that replaces `triage.md`'s. |
| `dss/runner.py` | The locked-down `claude -p` command line. |
| `dss/server.py` | The `dss` MCP server. |
| `dss/thread.py`, `jira.py`, `cerb.py`, `code.py`, `confluence.py` | The tools, one module per source. |
| `dss/worker.py` | Runs a function in another app's venv. |
| `dss/slack.py`, `dss/store.py` | The Slack bot and its saved answers. |
| `slack_manifest.yaml` | The bot's Slack app. |
| `tests/` | pytest suite. External services are faked. |
| `CLAUDE.md` | Rules for Claude Code sessions in this repo. |

## Setup

Requirements: Windows, Python 3.11 and [uv](https://docs.astral.sh/uv/).

1. `uv sync`
2. Install the `claude` CLI and log in once (`claude`). Triages run under that login.
3. **Cerberus:** a checkout of `darkvisiontech/DataDHPython` with its venv. Fetch it regularly, so the `code_*` tools see new release commits.
4. **Project index:** your own login. The approval covers read-only use on DSS tickets. Check that it covers you before you use it.
5. **Jira and Confluence:** a read-only account's Atlassian API token. One token serves both.
6. `copy .env.example .env` and fill it in. `.env` is git-ignored.

Check the setup with one triage: `uv run python -m dss triage DSS-1234`.

## Usage

```powershell
uv run python -m dss triage DSS-1234                                 # print one analysis; no Slack
uv run python -m dss triage DSS-1234 --posted 2026-10-21_09-30-00    # an older ticket
uv run python -m dss triage DSS-1234 --post-file post.txt            # with the workflow's Slack post
uv run python -m dss triage DSS-1234 --thread <permalink of the post> # with its Slack thread
uv run python -m dss trial DSS-1234 --thread ... --post-file ...     # the same triage as a checked record
uv run python -m dss slack                                           # the bot
```

The analysis goes to stdout, and turns, tokens and cost to stderr. `--posted` is the ticket time, in local time, and defaults to now. `cerb_last_session` uses it to pick the reporter's last session before the ticket, so pass it for an older ticket. `--post-file` is a text file with the support-request workflow's post (summary, client, DVT), which the Slack bot passes on its own; it gives Claude the ticket facts when Jira is down.

`--thread` takes the workflow post's permalink (or its ts) and lets Claude read the thread with `slack_thread`, as the bot does. `--posted` then defaults to the post's time. Replies posted more than 5 minutes after the post are left out, so a triage of an older ticket sees what the bot would have seen, not the developers' answers. It needs `DSS_SLACK_BOT_TOKEN`; the bot token alone starts no bot.

Every triage, from the CLI or the bot, adds a row to `out/usage.csv`: run time, ticket, turns, seconds, tokens by kind and cost. With `DSS_TRACE=1`, its tool calls also go to `out/trace/<key>_<run time>.jsonl`, one JSON line each: tool, arguments, seconds, and the error or the result's size and first 2,000 characters. Both hold client data and stay in the git-ignored `out/`.

### Trial mode

`python -m dss trial` runs the triage record design (the DSS Triage Flow page) by hand, without changing the bot. It takes the same arguments as `triage` and runs the same locked-down Claude with the same steps and rules. Four things differ:

- Before Claude starts, code reads the `dss_ticket` block: the reporter, DVT, project folder, workflow, Toolbox Name, the reporter's Cerberus version and last failed run, and the prod version. A source that fails leaves its facts null and is noted; it doesn't stop the triage.
- Claude answers with one JSON object (the `triage` and `class` blocks), following [dss/triage_record.md](dss/triage_record.md) in place of `triage.md`'s Markdown answer section.
- Code checks the record against `docs/triage_record.schema.json`.
- Code renders a DM (the decision: status, cause, what to do, questions, suggested reply) and a thread reply (the proof: evidence, not proved, details, class, the facts code read). An answer that fails the check is shown as given, with the problems.

The command prints the DM, then the thread reply. Each trial is saved as `out/trial/<key>_<run time>.json`, never in `out/answers/`, the folder the bot reads to tell which tickets are done. The trial doesn't post to Slack; whoever runs it delivers the DM and the thread reply to the reviewer.

### Scoring answers

```powershell
uv run python -m dss eval out/eval/golden.jsonl out/eval/2026-10-30_baseline           # no Claude runs
uv run python -m dss eval out/eval/golden.jsonl out/eval/2026-10-30_baseline --judge   # plus a Haiku judge
```

The golden set has one JSON line per closed DSS ticket with its known resolution: the ticket and its time, the gate that should settle it, the outcome in one line, the evidence the run should read, phrases of known false leads, the Toolbox Name and Investigation Outcome, and where the answer comes from. `docs/golden.example.jsonl` is a made-up row to copy; the real rows hold client data and live in the git-ignored `out/eval/`. The run folder holds each ticket's answer as `<key>.md` and its trace as `<key>_<run time>.jsonl`. For each ticket, `eval` prints and saves (`scores.jsonl` in the run folder) whether the run read each evidence item, whether every Jira key, Confluence page and code `file:line` under Checked exists (looked up read-only, code at the commit the trace shows), any false lead found, whether Suggested labels name the Jira labels, and the length. `--judge` adds a locked-down Haiku (`claude -p` with no tools) saying whether the answer reached the outcome; each judgement is one more run under your login.

### The bot's Cerberus

The bot reads the Cerberus analysts run, not whatever branch a developer has checked out:

- `code_*` read the bot's own clone (`DSS_CERB_GIT`) at the commit of the version in the prod folder (`release: v1.0.4`), never a working tree. Each triage first fetches every branch, so new releases, fix branches and pushed UAT builds are seen. A UAT build made only on a developer's PC is invisible until it is pushed.
- `cerb_client_info` runs Cerberus's own client info reader from the prod wheel itself, unpacked into `DSS_CERB_PROD_CODE/<version>` the first time a version is seen. The Cerberus venv still provides the dependencies; only Cerberus's own code comes from the wheel.

```powershell
uv run python -m dss cerberus   # clone if missing, fetch, unpack the prod wheel, and say what each tool reads
```

A fetch or an unpack that fails is told to Claude as a note and doesn't stop the triage.

To try the tools by hand in Claude Code:

```powershell
claude mcp add dss -- uv run -q --directory <this repo> python -m dss mcp
```

### The Slack bot

Create the bot's own Slack app from [slack_manifest.yaml](slack_manifest.yaml) and set the `DSS_SLACK_*` variables. Don't reuse the DV Assistant's app: Slack hands each event to one of an app's open connections, so two bots on one app would each miss tickets.

The bot waits until 5 minutes after the post before it triages (`TRIAGE_DELAY_S`): the workflow posts the analyst's "Screenshot of error" right away, and analysts often add more in the first minutes. `slack_thread` downloads those screenshots, which needs the `files:read` scope; an app made from an older manifest needs that scope added and the app reinstalled.

Slack doesn't resend events the bot missed. On start it therefore triages every ticket from the last 24 hours that has no saved answer, oldest first.

To run it around the clock, run it under the reviewer's own Windows account, because the `claude` login belongs to that session. Register a task that starts the bot at logon and restarts it if it stops:

```powershell
$action = New-ScheduledTaskAction -Execute 'uv' -Argument 'run python -m dss slack' -WorkingDirectory '<this repo>'
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName 'DSS triage bot' -Action $action -Settings $settings -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME)
powercfg /change standby-timeout-ac 0   # never sleep on mains power; locking the screen is fine
```

After a reboot, someone has to log in before the bot starts again.

## Working together

- **Branches and reviews:** work on a branch and open a pull request to `main`. The other owner reviews it ([.github/CODEOWNERS](.github/CODEOWNERS); branch protection requires one approval). Nobody outside the two owners needs to review.
- **Before review:** `uv run pytest`, `uv run ruff check .` and `uv run ruff format .` all pass. Code uses tabs, single quotes and a 100-character line length.
- **Prompt and tool changes:** test with `python -m dss triage` on a few real tickets and compare the answers before and after. Put the comparison in the pull request.
- **Slack changes:** use a dev Slack app (the same manifest under another name) and a test channel (`DSS_SLACK_CHANNEL`). Only one copy of the production bot runs, on the reviewer's PC.
- **Status log:** add an entry to [docs/STATUS.md](docs/STATUS.md) for each work session.

## Safety rules

- **Read-only everywhere.** The tools send only GET requests and git reads, and Claude has no other tools. Adding a tool that writes (to Jira, a Slack channel, the share or Cerberus) needs both owners to agree. The project index approval covers read-only use only.
- **Nothing reaches an analyst without a person.** Analyses go to the reviewer's DM only.
- **No secrets or client data in git.** Analyses contain client names and project details. `.env` and `out/` are git-ignored, and the repo stays private.

## Environment variables

All of them can go in `.env`; see [.env.example](.env.example).

| Variable | Used for |
|---|---|
| `DSS_CERB_PY`, `DSS_CERB_CWD` | The Cerberus venv's python and checkout: `cerb_client_info` and `cerb_read_sheet` run in it; `code_*` read the checkout when `DSS_CERB_GIT` is not set. |
| `DSS_CERB_GIT` | The bot's own Cerberus clone, read by `code_*` and fetched before each triage. |
| `DSS_CERB_PROD_CODE` | Where the prod wheel is unpacked, one folder per version, so `cerb_client_info` runs the Cerberus analysts installed. |
| `DV_PROJECT_INDEX_USER`, `DV_PROJECT_INDEX_PASSWORD` | Login for the project index. Sent as HTTP Basic auth over plain HTTP. |
| `DV_PROJECT_INDEX_URL`, `DV_PROJECT_INDEX_ROOT` | The index address and the share it indexes. Default `http://192.168.25.120` and `\\192.168.24.252\projects`. |
| `DV_PROJECT_INDEX_MAX_MB` | The biggest file the sheet, document, image and client info tools copy from the share, in MB. Default 1024. Copies stream to disk, are reused for 15 minutes and deleted after a day; `cerb_read_text` still reads at most 50 MB. |
| `DV_CERB_PROD_DIR` | The folder analysts install Cerberus from. Default `F:/DarkVision Dropbox/Analysis/CERBERUS/prod`. |
| `DV_CERB_REF` | A Cerberus commit for `code_*` to read instead of the prod release commit. |
| `CONFLUENCE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_TOKEN`, `CONFLUENCE_SPACES` | The read-only Confluence account, and optionally the spaces to search. |
| `JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN` | The read-only Jira account. Each defaults to its `CONFLUENCE_*` value. |
| `DSS_SLACK_BOT_TOKEN`, `DSS_SLACK_APP_TOKEN` | The bot's Slack tokens. `slack_thread` reads with the bot token, also in `triage` and `trial`. |
| `DSS_SLACK_REVIEWER` | The Slack member id whose DM gets the analyses. |
| `DSS_SLACK_CHANNEL` | The channel to watch. Default #data-dev-support. |
| `DSS_TRACE` | `1` writes each triage's tool calls to `out/trace/`. |

## Origin

This repo was split out of dv-auto-setup-agent (Arsenii Stolbov) on 2026-10-21, at commit `b3f182e`. `runner.py`, `worker.py`, `confluence.py`, `cerb.client_info` and the Slack formatting helpers are copies cut down for triage, so fixes made there don't reach this repo on their own. The longer-term plan in that repo is a shared `dv-llm` package for the runner and tools, which both repos would then depend on.
