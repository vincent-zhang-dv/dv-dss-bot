# Status (newest first; add an entry per work session)

### 2026-11-25: DSS Bot Review page reads the Slack connector's quoted output (Vincent, with Claude)
- **Why:** the page showed only DSS-1256 to 1258. The Slack connector now puts `> ` before each
  line of a message body, so the page no longer matched the bot's post header and skipped every
  post after DSS-1258, with no error ("No new posts or replies").
- **Done:** the page's parser takes the `> ` off a body when every line has it (channel and thread
  reads), and leaves out the "Edit & copy draft button" line as it does the review button.
  Republished to the review page (version 8).
- **Checked:** `review/slack_parse.test.js` (new test on quoted output) and
  `tests/test_review_page.py` pass; the parser finds DSS-1263 in the live channel output.
- **Not done / open:** someone with Contributor access must press Refresh from Slack once to add
  DSS-1259 to 1263.

### 2026-11-20: "Edit & copy draft" button; repo clean-up (Vincent, with Claude)
- **Why:** Tim suggested approving or editing the drafted reply from a button. Sending it as the
  developer needs the user scope `chat:write`, and any new scope waits for a workspace admin, so
  that path (branch `approve-send`, not merged) is parked. Copy and paste needs no new scope and
  keeps the rule that the bot never posts in DSS threads.
- **Done** (branch `copy-draft`):
  - A post whose record passed its checks gets an "Edit & copy draft" button beside the review
    button. It opens a pop-up with the Draft Reply to edit and copy; the developer posts it in
    the DSS thread themselves. The button carries the ticket and the draft (at most 700
    characters), so the pop-up reads no saved file. Replays posted with `--post-test` get it too.
  - Clean-up: removed the unused frontend-design skill, merged branches and stale worktrees.
- **Checked:** `test_slack.py` and `test_replay.py` pass (75). A live click needs the bot
  restarted on this code.
- **Not done / open:** `approve-send` (send as the developer, needs the admin-approved scope and
  Annika's agreement on the DSS rule).

### 2026-11-19 (evening): plain writing for the analyst, replies in the sender's voice (Vincent, with Claude)
- **Why:** Phase 6 of the Reply Quality Tracker (developer-ready replies). Of 18 rated replies, 8
  were Correct, 9 Partial and 1 Incorrect. Besides wrong causes, reviewers deleted detail nobody
  asked for ("not your plugs") and added an owned next step ("I will fix it and rerun it for
  you"). Abhi proposed simple-English rules (ASD-STE100); the bot's replies averaged 22 words a
  sentence against 19.5 in the reviewers' rewrites.
- **Done** (branch `reply-style`):
  - `triage_record.md`: "Writing for the analyst" covers the summary, the analyst's actions and
    the reply. It asks for short sentences (20 words in a step, 25 elsewhere), steps that start
    with a verb, conditions first, active voice, exact paths and numbers, and nothing the
    analyst doesn't need to act on. The developers' thread stays technical.
  - The reply speaks as the developer who approves and sends it ("I"). It gives the verdict, the
    cause with its numbers, and ends with the next step and who takes it. Three examples come
    from known causes of tickets outside the replay set.
  - Schema: the reply may have 4 sentences (was 3). For a Cerberus bug it says the developer
    will fix it and regenerate the output (was "promise no other work").
  - `triage.md`: the DFR rule bans only a DFR the analyst didn't ask for. The wording merged in
    PR #23 also blocked DSS-1238's DFR, which Vincent's rewrite asks for.
- **Checked:** 315 tests pass. Seven replays of tickets whose latest reply was rated Partial or
  Incorrect ($6.31):
  - Sentences went from 22.2 to 17.3 words on average; 9 of 25 were over 25 words, now 2 of 23.
  - DSS-1239 and DSS-1236 now read like Vincent's rewrites; DSS-1249 no longer calls it a bug.
  - DSS-1191 lost its fix (remove the suppression, re-export, rerun), and DSS-1238 lost its DFR.
  - After the 4-sentence and DFR fixes, three more replays ($2.57): DSS-1238 ends with the DFR as
    in Vincent's rewrite; DSS-1095 has no DFR and says "I'm checking your radius caliper";
    DSS-1191 still gives no fix and no analyst step (it calls the empty splines intended and
    doesn't say they were suppressed).
- **Not done / open:**
  - No code check counts sentence length yet. Add one if the rule stops holding.
  - DSS-1249 still doesn't cite the LAS SOP page, and DSS-1081 still names Zscaler.
  - DSS-1191 stays unstable across runs: the investigation, not the writing, decides its fix.

### 2026-11-19: narrower DFR rule, replay cap 40 (Vincent, with Claude)
- **Why:** Annika's ratings of the 2026-11-17 replays had four sections worse than on
  2026-11-12 and one better. DSS-1095 added an "if you need ..., raise a DFR" to a
  works-as-designed answer; she deleted exactly that. DSS-1191's run hit the 30-call cap on the
  call that lists the VizApp suppression mask and then blamed CatCall. On live DSS-1260 the bot
  offered a DFR to fill two baseline bins that have no calibration perfs. Abhi said that can't
  work, and Cerberus' error itself suggests using one bin.
- **Done** (branch `narrow-dfr`): `triage.md`'s DFR rule applies only to what the analyst asks
  for. It never adds a DFR as an "if you need ..." to a works-as-designed answer, and never
  suggests one when the request can't work (the data doesn't exist) or the error message, a
  current Confluence page or a past issue shows it can't work (not only that Cerberus doesn't do
  it now); it says why and gives the supported way.
  `replay.MAX_CALLS` is 40 (README too). Live triage has no call cap. These replays ran with the
  DSS-1260 example in the rule split over two lines, so the replay (which hides lines naming the
  ticket or a later one) cut it mid-sentence; it is on its own line now.
- **Checked:** 315 tests pass; ruff clean on `replay.py`. Replays on the branch ($5.84):
  - DSS-1260: no DFR; it explains why six bins can't be calculated and gives fewer bins or one
    bin, as Abhi did.
  - DSS-1095 (two runs): no DFR either time. Both summaries name the radial tracks the analyst
    asked about.
  - DSS-1238 and DSS-1256 still ask for a DFR, as they should.
  - DSS-1258 (after rewording "rules it out" to "shows it can't work"): still asks for a DFR,
    as Product approved one.
  - DSS-1081 (two runs): both summaries keep the error code and drop "table export". The
    2026-11-17 summary was run-to-run variation, not a rule.
  - DSS-1191 (two runs) splits. One read the Masks folder and pointed at the VizApp suppression
    (close to Annika's answer). The other used its 40 calls elsewhere, never read the mask, and
    proposed a Cerberus bug (np.max vs nanmax) that Annika marked overclaimed on 2026-11-12.
- **Not done / open:**
  - DSS-1191 is unstable either way; one ticket, so no rule yet.
  - DSS-1095 still misses that the radii and curves calipers came from different folders.
  - Not merged: the PR waits for the owners.

### 2026-11-18 (evening): code checks cited evidence against the trace (Vincent, with Claude)
- **Why:** Vincent's goal: a model only where reasoning is needed, scripts for the rest. On 20
  saved runs (24 tool calls, 26 turns a run), nothing checked that cited evidence was read, and
  the fixed reads (thread, the ticket's Jira issue, project, last session) were the model's.
- **Tried, held back:** the 2026-11-13 `prefetch-facts` work, ported onto main (branch
  `prefetch-port`, local only, 313 tests pass). Replays of DSS-1256, 1255, 1254, 1239, 1222 and
  1249 on main and on the port: model turns 184 to 168 and tool calls 172 to 154 (both about
  -10%), cost $4.23 to $4.41, time the same; DSS-1256 half the turns with the same answer. But
  DSS-1255 went from "likely" (3 of 3 runs on main) to "proved" (3 of 3 on the port), an
  overclaim: the log never showed why pyarrow failed to load. Its fix advice varied on both sides,
  so that part was chance. Vincent's choice: ship only what doesn't change what the model is told.
- **Done** (branch `evidence-check`, from main `41dee7f`): the port's evidence check alone.
  `record.evidence_gaps` marks an evidence item whose source the trace doesn't show, in the
  thread, with a count in the post; the answer always goes out. Changed from the port: a file
  line counts as read by `code_read`, `matlab_read` or a grep hit, whichever tool the item
  names. On today's real runs the port's version marked 3 refs, all false (the bot cited a
  `matlab_read` and a grep hit as `code_read`); this version marks none.
- **Checked:** whole suite 284 passed; ruff clean on the changed files. 38 real records, 251 evidence refs, 0 marked. No real
  run has cited an unread source yet, so only the unit tests show it catching one.
- **Not done / open:**
  - Prefetch, retested on top of the proved rule (branch `prefetch`): on the six tickets, model
    turns 181 to 154, tool calls 169 to 142, cost $4.16 to $3.43; the same statuses on all six,
    none proved with an unread link; no evidence marks. DSS-1249 suggested a peak-preserving DTN
    again, as main did; its gap (the LAS SOP page is not found) is open on every branch.
  - Step 3 (a scripted past-issue search) dropped by Vincent: the search words are judgement.
- **Then, the proved rule** (branch `proved-rule`, on `evidence-check`, Vincent's choice): 13 of
  20 "proved" answers today also listed not-proved checks. Most were follow-ups after a read
  cause (where a fix lands, a stale file); DSS-1255's was a link of the cause itself. The schema
  now has `follow_ups` beside `not_proved`, and proved needs `not_proved` empty; claude's
  structured output enforces it during the run. Both still show as the developers' Confirm todos.
  Replays of the six tickets: DSS-1256 stays proved (with a follow-up), DSS-1255 likely, DSS-1254
  needs info, DSS-1222 and 1249 likely (1249's headline now "likely by design", asking developers
  which of MATLAB and Cerberus holds), DSS-1239 proved to likely because the replay's spreadsheet
  changed after the ticket; follow-ups in all six; $4.16 against main's $4.23. Suite 286 passed
  (`test_web_links_name_only_what_the_clone_has` failed once and passed on rerun, on main too).

### 2026-11-18: DSS Bot Review page in the repo, step 1 of learning from reviews (Vincent, with Claude)
- **Why:** the team approved learning from the developers' reviews: reviews on the DSS Bot Review
  page become golden cases and drafted fixes, checked by replays, merged by the owners. Step 1
  keeps the page's source next to the bot whose posts it reads.
- **Done** (branch `review-link`):
  - `review/dss-bot-review.html`, the page's source (no client data; tickets and reviews live in
    its claude.ai database). `review/slack_parse.test.js` tests its Slack parser on made-up data
    with Node's own test runner; `tests/test_review_page.py` runs it, and checks the page against
    `record.render`'s headings and the bot's review link.
  - Each live bot post has a "Please review the bot's report" button, opening its ticket on the
    page (`slack.REVIEW_PAGE`, `#DSS-<n>`). A link in the text got a claude.ai preview card under
    the post (Vincent saw it); `unfurl_links=False` removes the DSS post's preview too (tried), and
    a button gets none. Its click is acknowledged and does nothing else. Replays and trials don't
    get it.
- **Checked:** `node --test`: 5 passed; whole pytest suite: 271 passed. Ruff clean on the changed
  files. The page's refresh ran in
  Vincent's browser through his Slack connector and wrote the same DSS-1256 ticket the tests'
  parser builds.
  - Draft posts to #data-dev-support-assist-test: a link in the text got a claude.ai preview
    card; a button shows the DSS preview only. PR #18.
- **Step 2 done** (branch `learn-reviews`, on `review-link`):
  - `python -m dss learn <export>` (`dss/learn.py`): each submitted review in an export of the
    page's database becomes a golden row in `out/eval/golden_reviews.jsonl`, and a digest lists
    the reasons by ticket count, those worth a fix (on 2+ tickets, or a post marked harmful) and
    each ticket's corrections. Plain code, no model.
  - `dss/eval.py`: a golden row's gate is optional (Vincent's choice): reviews don't record one,
    and eval only prints it. Rows without one show `gate -`. The missing-fields message no
    longer names gate; its test changed with it.
  - Ruff's safe fixes in `eval.py` and `test_eval.py` (regex flag names, formatting). Its SIM110
    finding in `eval.py` (`line_count` loop) is older and left as is.
- **Checked:** whole suite 277 passed (`test_the_analysts_build_is_exported_from_git_without_a_wheel`
  failed once and passed on rerun, as on 2026-11-17). A dry run on the real export found the
  DSS-1256 review not submitted yet, so no rows.
- **Step 3 done** (same branch), with Vincent's choices: eval learns replays, a weekly reminder
  to Vincent starts the run, and the run may replay up to 6 tickets before asking.
  - `dss eval` scores a replay folder: without `<key>.md`, the newest `<key>_<time>.json` record's
    post and thread is the answer (a stopped replay is "no answer saved"). The judge and the
    evidence check apply; a record has no Checked section, so no citations. Checked on the
    DSS-1256 replay of today: read 2/2.
  - `.claude/skills/dss-learn/SKILL.md`: the weekly run. Export the page's database, `dss learn`,
    draft fixes (code, then known causes, then tool notes, then `triage.md`), replay up to 6
    tickets, `dss eval --judge` before and after, commit; pushing and the PR wait for an owner.
  - Routine "DSS learning reminder" (claude.ai, `trig_014ZGAdkWcyMsnH6AwMNx7en`, Mondays 16:30
    UTC = 8:30 in BC in winter, 9:30 in summer): reads the page's `reviews` with ArtifactData and
    DMs Vincent only when some were submitted in the past 7 days. Slack is its only connector. A
    test run read the database and, with nothing submitted, sent nothing.
- **Checked:** whole suite 279 passed; ruff clean on the changed files except the older SIM110.
- **Not done / open:**
  - The first real run waits for submitted reviews (none yet).

### 2026-11-18: lessons from how the developers handled DSS-1246 to 1256 (Vincent, with Claude)
- **Why:** on DSS-1256 (a custom plot for a sales trial) the live bot told a developer to make
  the plot; Annika's first question was "is there a DFR?", and Dimitri then raised one. The
  process for custom asks needing Dev support is in a 2025-05 Slack thread (channel
  C0515F6HWNT), not in Confluence: a DFR is raised (marked Needs Product Approval and Dev support), linked from the
  DSS; the developer comments the effort and what it displaces in the DFR and tags Product, who
  approve or reject. The Custom Requests Guidelines page (last edited 2024-07-30) is outdated.
- **Done** (same branch):
  - `triage.md`: the DFR rule now covers custom outputs (plots, tables, deliverables) as well
    as changes against Cerberus. The bot looks for a DFR key in the thread and the Jira links;
    without one, the reply asks for a DFR; developer actions are the DFR steps, never building
    the output before Product approves. The schema's `actions.developers` says the same.
  - `triage.md`: after finding faulty code, grep its pattern in other modules (DSS-1251: Annika
    noted sibling files already had the fix); check Cerberus' version pins before an install
    action (DSS-1255: pyarrow needs numpy 2, Cerberus pins numpy < 2).
  - Known cause `defender-scans-cerberus-install` (DSS-1254): Windows Defender scanning the DLLs
    of a Cerberus outside C:\code; the fix is the new cerb.bat (installs in C:\code\cerberus).
- **Not done / open:**
  - Annika asked for the live bot answers on the feedback (DSS Post Review) page, to rate them
    as they come.
  - Vincent plans an analyst-facing troubleshooting entry: "for a custom request, request a
    sales DFR".
  - DSS-1253 did not link the existing DTN-3638; DSS-1250's Defender first-run scan of the ML
    script is not a known cause (different install, not verified here).

### 2026-11-17: lessons from Vincent's ratings of six replays (Vincent, with Claude)
- **Why:** Vincent rated the 2026-11-16 replays (DSS-1222, 1229, 1236, 1238, 1239, 1249). The
  bot was right on the code bugs and the failed run (1222, 1229, 1239) and wrong on all three
  "intended or bug" tickets: DSS-1236 (harmful: misread which white line the analyst meant, then
  called it works-as-designed), DSS-1238 (proposed relaxing a product requirement) and DSS-1249
  (harmful: called it a bug; the SOP says the LAS doesn't preserve peaks). Its one Confluence
  search in DSS-1249, four words, found nothing; "LAS resampling" finds the SOP page.
- **Done** (branch `review-lessons`), code where code can settle it, rules where it can't:
  - `matlab_read` gives the date the MATLAB file last changed; `confluence_search` says to use 2
    or 3 words, since a page must contain every word.
  - `triage.md`: name the exact element the analyst points at and compare a "used to look like
    this" image; "works as designed" needs the path that made that symptom. When any two of the
    Confluence SOP, the code and MATLAB CAT disagree, give each with its age and ask the
    developers to confirm (replaces "the code wins"). A requested change that neither MATLAB nor
    Confluence describes and that goes against current Cerberus: suggest a Data Follow-up
    Request (DFR).
  - `triage_record.md`: `cause` covers each reported issue (DSS-1222 put issues 2 and 3 under
    "Also found"); `secondary` is only for causes the analyst didn't report; for a Cerberus bug
    the analyst reviews the regenerated output; the reply leaves out that their data is not at
    fault.
- **Then, against prompt dilution** (Vincent: agents only where they are needed), code took over
  what it can settle, with no new agent:
  - The answer format moved out of the prompt: claude's `--json-schema` checks the answer and
    asks Claude again until it fits (tried: a rule broken on purpose came back fixed in 3
    turns, also under the bot's lockdown flags). `record.answer_schema` builds it from the
    record schema, references inlined (the API takes no top-level `allOf`). Each field's
    guidance is now its schema description; `triage_record.md` keeps the general rules and the
    known causes. The reply is capped at 700 characters (past replies: median 498, p90 730).
  - The system prompt goes in a file (`--system-prompt-file`), so its size is no longer bound by
    Windows' command line; only the schema is on it (14,578 characters with the replay block).
  - Known causes are sent by workflow: a PE ticket doesn't get WI or FF causes
    (`record.for_workflow`); combo and unknown get them all.
  - Rules travel with the tool results they apply to: the source-conflict rule with
    `confluence_page` and `matlab_read`, the VizApp support channels with `vizapp_*`.
  - System prompt: 22,444 → 14,145 characters (unknown workflow), 13,511 (WI), 12,811 (PE).
- **Checked:** whole suite, 268 passed (3 runs). Ruff clean on the changed files (6 older
  findings in `cerb.py`, `code.py`, `eval.py`). The CLI accepted the full answer schema on a
  Haiku test. Replayed the 12 rated tickets (Vincent's six, Annika's six) on this branch: all
  passed the record check, $7.18 in all. DSS-1236 now points at the missing collar gap in the
  Classification curve against MATLAB (likely); DSS-1238 matches Vincent's correction (works as
  designed, suggest a DFR, no developer change) and asks to confirm a Cerberus/MATLAB rule
  difference; DSS-1222's cause covers all three issues. The runs are on the DSS Post Review page
  (version 9) beside the rated ones, with a word diff per section, for Vincent and Annika to rate.
- **Not done / open:**
  - DSS-1249 still proposes peak preservation: its one Confluence search ("LAS depth step") did
    not return "Export Deliverable LAS from Matlab", whose 2026-07-06 version says the resampling
    does not preserve peaks and that's OK.
  - DSS-1239's reply still says it is "not your plugs" and "nothing needs changing on your side",
    though the reply's schema description says to leave that out.
  - Ratings of the 2026-11-17 runs on the review page decide the merge.
  - Writing the reply in a separate short call (plan step 5): only if the replays still show
    writing misses.
  - Terminal-log instructions are still sent for pre-1.1 sessions (small; left as is).

### 2026-11-16: VizApp code and release notes as a source (Vincent, with Claude)
- **Why:** VizApp writes most of what Cerberus reads (dvcalipers, bookmarks), so a DSS issue can
  start in VizApp; then the analyst should go to the VizApp support channels named in the
  2025-05-26 reminder in #data-dev-support, not to the data developers.
- **Done** (branch `vizapp-knowledge`): `reply-feedback` merged as PR #15 (`f3ec825`). New
  `dss/vizapp.py`: `vizapp_search` (GitHub code search of DVCode master) and `vizapp_read` (one
  file, or its lines containing a text, at master; in a replay at master's last commit before the
  post), through the gh CLI, GET only. DVCode is over 4 GB, so there is no clone. `vizapp_read`
  waits for the past-issue searches like `matlab_read`. `triage.md` Gate A: check how VizApp makes
  the data and its release notes (`Hades/CHANGELOG.md`); if VizApp is the cause, send the analyst
  to #dh-ask-support (Mon-Fri 9:00-17:00), otherwise to the after-hours channel in the reminder.
- **Checked:** `uv run pytest` on `test_tools`, `test_runner`, `test_record`, `test_triage` and
  `test_replay`: 184 passed. Both tools ran once against GitHub by hand (a search for dvcaliper
  and the release notes' caliper lines). Record prompt: 21,661 characters. No live triage.
- **Not done / open:**
  - The after-hours VizApp channel is private and the bot can't name it: the reply links the
    reminder instead.
  - GitHub code search sees today's master only, so a replay's search can show later code; a read
    of a match is at the cutoff.
  - The live bot still runs `684b68d`: neither PR #15 nor this branch is pulled there.

### 2026-11-16: shorter, clearer replies and todos from Annika's ratings (Vincent, with Claude)
- **Why:** Annika rated the six replays of 2026-11-12 on the DSS Post Review page: 46 of 54
  sections correct, 8 partial, none incorrect. All eight were in the analyst todos, the developer
  todos or the draft reply; the diagnosis sections were all correct. Vincent: make the replies
  more concise and clear.
- **Done** (branch `reply-feedback`, prompt only):
  - `triage.md`: from a page's steps, give what a step is for, not its local setup (DSS-1081: "be
    on the DV network", not VPN and network settings). Name Cerberus steps as analysts see them,
    not by function names, and say what Cerberus needs rather than how its screens behave
    (DSS-1081, DSS-1186).
  - `triage_record.md`: the reply says "works as intended" when it does and leaves out side
    facts (DSS-1095). Analyst todos start with a check in their own tools, then the fix, then each
    rerun step in order, and a works-as-designed answer still checks the inputs the evidence shows
    (DSS-1095, DSS-1191). Developer todos and "Confirm" checks hold only steps that confirm or fix
    this ticket's cause (DSS-1191).
  - Two known causes: `techlog-license-server-off` (DSS-1081, from its thread) and
    `combine-sheet-pass-name` (DSS-1186; Vincent confirmed the wrong pass name was the cause).
  - The record prompt is now 21,194 characters, over the test's 20,000 guard (main was at
    19,951). Vincent chose to raise the guard to 24,000; the whole command line is 21,586 of
    Windows' 32,767.
- **Checked:** `uv run pytest` on `test_triage_record`, `test_record`, `test_triage`,
  `test_replay` and `test_runner`: 159 passed. No live triage or replay run yet.
- **Not done / open:**
  - CLAUDE.md asks for real triages before and after a prompt change: replaying the six (about $3)
    and comparing with Annika's corrected text is the check, waiting for Vincent's go.
  - The replays of DSS-1081 and DSS-1186 won't see their own new known causes (a replay leaves
    out causes learned from the replayed ticket or later).
  - The live bot was pulled to `684b68d` and restarted on 2026-11-13; the 2026-11-13 entry
    below was written before that.
  - Annika's note for DSS-1081 still doesn't match its thread; Vincent thinks the note is wrong.

### 2026-11-13 (late afternoon): code compares the versions, checks evidence against the trace and shortens the client info (Vincent, with Claude)
- **Why:** the four gaps left open by the entry below. Vincent's rule for them: code does what
  code can settle for certain, the model decides the rest, and no change may make the replies
  less accurate.
- **Done** (branch `prefetch-facts`, on top of the entry below):
  - **Gate B's version comparison** (`record.version_check`): when the reporter's version and
    the prod version are both release numbers, the prompt says which is older, that they are
    the same, or that they are two builds of one release (`1.0.4+uat20261031` against `1.0.4`).
    Otherwise it says nothing and Claude compares as before. On 26 saved trials: nothing for 11
    (no reporter version), the same for 10, two builds for 4, older for 1.
  - **Evidence against the trace** (`record.evidence_gaps`): for a record that passed, code
    checks each evidence ref. A ref that names a tool needs a call of that tool that got an
    answer in this run (code's reads count); a `code_read` or `matlab_read` ref of one
    `path:line` or `path:first-last` needs a call that read those lines. A ref that fails is
    marked in the thread reply ("not shown by the trace"), the post gets one "Evidence to
    check" line, and the saved trial lists it under `evidence_gaps`. It is a mark for the
    reviewer and never refuses an answer; an error in the check itself becomes a note. `computed`
    and refs that hold several locations are left alone.
  - **Client info size** (`cerb.joints_compact`, in the `cerb_client_info` tool only): a joint
    column that holds one value for every joint is given once under `every_joint`. The copied
    `cerb.client_info` is unchanged.
  - `triage_record.md` said the record has no summary or description and told Claude to call
    `jira_get_issue`; since the entry below they are in the prompt, so it now says that.
- **Left with the model, on purpose:**
  - Known-cause matching: the known causes have no signature code can match, and 4 of 24 saved
    trials named a listed cause. A wrong match by code would hurt the answer.
  - The "up to three Jira searches" rule: 1 to 3 searches in each of 25 traces, never broken, so
    no cap was added.
  - One Opus run per ticket: code cannot tell for certain which tickets a smaller model answers
    as well.
- **Checked:**
  - `uv run pytest` on `test_record`, `test_runner`, `test_tools`, `test_replay`, `test_slack`
    and `test_triage`: 228 passed. `ruff check` and `ruff format --check` show only the findings
    listed in the entry below.
  - `evidence_gaps` on 17 saved records with their traces (124 evidence refs, 30 of them line
    refs): none marked. A first version marked two refs that hold several locations; those are
    now left alone.
  - `joints_compact` on the 5 real client info files the saved traces read (84 to 553 joints),
    read through the Cerberus venv: putting `every_joint` back gives every joint value as
    Cerberus read it, and the answer is 42% to 73% shorter (154,773 characters to 42,182 for the
    largest).
  - One regular-mode trial on the branch (`python -m dss trial`, nothing sent to Slack) on
    DSS-1251, against the bot's run of 2026-11-11: the same cause, the same two lengths in the
    reply and the same three Casing Record rows, status `proved` both times, no evidence marked, `cerb_client_info` 154,214
    characters before and 42,182 after, $0.39 against $0.43. Today's run also found the DTN
    ticket raised since, so the two answers differ in what is known, not in the cause.
- **Not done / open:**
  - Nothing is committed or pushed, and the live bot's checkout is untouched.
  - The system prompt is 19,993 characters, 7 under the tested limit. New instructions have to
    go into the prompt code builds, or something has to come out first.
  - `joints_compact` wraps copied code's answer. It may help dv-auto-setup-agent too, if its
    client info answer is as large; for the owners to pass on.
  - The evidence check has not yet marked a ref in a real run, so how often it will, and whether
    the marks are right when it does, is not known.
  - SIM108 in `dss/cerb.py` is in `cerb_client_info`, which this change touches; it asks for a
    ternary, so it is left for Vincent to decide.

### 2026-11-13 (afternoon): code makes the fixed reads and Gate C's input check (Vincent, with Claude)
- **Why:** Vincent wants the bot to use the model only where a task needs reasoning, and plain
  code for everything else. In the newest 10 traces Claude spent 1 to 4 calls per run on answers
  code had already read or could work out: its own `jira_get_issue` (10 of 10 runs),
  `cerb_last_session` (6), `cerb_find_project` (4), the first `cerb_list` of the project (3) and
  `cerb_prod_version` (1). Gate C's input check was Claude reading that listing.
- **Done** (branch `prefetch-facts`, the checked-record path only; `python -m dss triage` is
  unchanged):
  - `record.read_ahead` keeps what code already read (the Jira ticket, the reporter's last
    session) and adds one listing of the project folder.
  - `record.inputs` does Gate C's check on that listing: the client info file and `Bookmarks`
    for every job, the baseline calculation for PE, `DVCaliper_Files` for WI and CI, by the
    names Cerberus' project picker checks (`proj_select.py`). The answer is `dss_ticket.inputs`
    (already in the schema). A listing that fails or is cut short leaves it out, and in a replay
    so does an input changed after the ticket; Claude then checks as before.
  - The prompt gives Claude the three reads, each as the tool's own answer, and tells it not to
    make those calls again. `triage.md` got one sentence for it; the rest is in the prompt,
    because the system prompt is at its tested limit of 20,000 characters.
  - The reads go into the trace marked `by: code`. A replay's leak check covers them; Claude's
    call count, the replay's 30-call cap and the step-order check leave them out.
    `replay.calls` now counts through `record.traced_tools`.
  - `slack_thread` stays Claude's call: a screenshot reaches Claude only as a tool result.
- **Checked:**
  - `uv run pytest` on `test_record`, `test_replay`, `test_slack`, `test_runner` and
    `test_triage`: 153 passed. `ruff check` passes on the changed files, and
    `ruff format --check` on all but `tests/test_eval.py` (not touched).
  - Two regular-mode trials on the branch (`python -m dss trial`, nothing sent to Slack) on
    DSS-1253 and DSS-1254, against the bot's own runs of 2026-11-12. Fixed calls by Claude: 2
    and 3 before, none after. Both records pass the check, `inputs` is filled, and the order
    check passes. Cost is about the same ($0.70 and $0.82 against $0.67 and $0.65): the fixed
    calls were 2 or 3 of about 30.
  - The answers can't be compared one to one: today's runs see a day more of the project
    folder (DSS-1254's report had been made by then) than the bot saw at the post.
- **Not done / open:**
  - Nothing is committed or pushed, and the live bot's checkout is untouched.
  - The two trials started at the same time, and one noted `git fetch failed` on the Cerberus
    clone (both fetch into the same clone). Not looked into.
  - `python -m dss trial` with its output redirected to a file fails at the final print on
    Windows (`UnicodeEncodeError`, the `≈` in the footer), after the trial is saved. Existing
    behaviour, not changed.
  - ruff findings in files this change does not touch, left as they are: SIM108 in
    `dss/cerb.py` and `dss/code.py`, FURB167 and SIM110 in `dss/eval.py`, formatting in
    `tests/test_eval.py`.
  - Other gaps from the same audit, for the owners to decide: the known causes have no
    signature code can match; live runs don't check the "up to three Jira searches" and
    "evidence was read" rules; `cerb_client_info` returns about 85,000 characters; every ticket
    gets one Opus run whatever its size.

### 2026-11-13: the replay work committed for the merge into main (Vincent, with Claude)
- **Why:** Vincent read the six replays of 2026-11-12 and asked to merge the replay PR (#14).
- **Done:** no code change. The branch's work since `4e4a3c0` (the four 2026-11-12 entries
  below: the thread as raised with the screenshot, the Confluence age mark, bot and developer
  time, the six replays) committed on `trial-replay` and pushed to PR #14.
- **Checked:** `ruff check` and `ruff format --check` pass on the six changed Python files.
  `uv run pytest` on `test_confluence`, `test_replay`, `test_thread`, `test_record`,
  `test_triage` and `test_runner`: 149 passed.
- **Not done / open:**
  - The live bot's checkout is not pulled or restarted. From the next pull it also gets the
    `may_be_out_of_date` mark on Confluence pages and the `triage.md` rule that goes with it.
  - CLAUDE.md's few real triages before and after a `triage.md` change were not run for that
    rule; the six replays ran with it.
  - ruff findings in files this change does not touch, left as they are: SIM108 in
    `dss/cerb.py` and `dss/code.py`, FURB167 and SIM110 in `dss/eval.py`, formatting in
    `tests/test_eval.py`.
  - PR #14 has no review from Annika.

### 2026-11-12 (night): the six replays run again on the final replay code (Vincent, with Claude)
- **Why:** Vincent wants the six DSS tickets Annika picked replayed with everything in place,
  and the answers on the DSS Post Review page for the owners and Sean to rate.
- **Done:** no code change. `python -m dss replay … --go` on DSS-1081, 1086, 1095, 1163, 1186
  and 1191, nothing sent to Slack; results in `out/replay/2026-11-12/`. Total ≈ $3.10; 29 s to
  2 min and $0.18 to $0.87 each.
- **Checked** (each answer read against the real thread and Annika's note): none refused, none
  over the $1.50 cap.
  - DSS-1163 proved the cause the developers found (the bars' sort order in the erosion plot).
  - DSS-1086 (a full disk, not Cerberus), DSS-1095 (works as designed) and DSS-1191 (wall loss
    suppression) match. DSS-1191 used the raiser's own follow-up 18 minutes after the post,
    which the as-raised rule lets a replay read.
  - DSS-1186 found the right sheet but blamed its column headers; the real fix was a pass name
    in it. The sheet changed after the ticket, and the replay flagged one possible leak.
  - DSS-1081 said Techlog could not reach its license server and pointed at the network; the
    server was switched off.
  - DSS-1095 stopped at the 30-call cap.
- **Outside the repo:** the review page shows the six replays with their real threads and Jira
  outcomes in Annika's session; Vincent's and Sean's sessions have no tickets. Ratings are open:
  each reviewer sees the others' as they are given, submitted or not.
- **Not done / open:** Annika's note for DSS-1081 describes plot edits on a branch, not a
  Techlog license: it may belong to another DSS ticket. `developer_time` counts every reply
  that is not the raiser's or a bot's, so a manager's or another analyst's reply counts
  (DSS-1191's 19 h 36 min ends at one).

### 2026-11-12 (evening): bot time and developer time on each replay (Vincent, with Claude)
- **Why:** Vincent wants to compare how long the bot and the developers take on each DSS.
- **Done** (branch `trial-replay`): after each replay, the command reads the ticket's whole thread
  (only then, so Claude never sees it) and saves `developers` with the result
  (`replay.developer_time`): developer replies, first and last reply, minutes from the post to
  the first (`response_minutes`) and from the first to the last (`solving_minutes`). Developers
  are the people who replied other than the analyst who raised it; the workflow doesn't count.
  The summary line gives the bot's time and cost next to it. `solving_minutes` is elapsed time,
  waits included, not hours worked.
- **Checked:** `uv run pytest`: 262 passed. Live, read-only, on the six replayed tickets: bot
  28 s to 1 min; developers 6 min (DSS-1081) to 19 h 36 min (DSS-1191), first replies 30 s to
  38 min after the post; added to their saved results in `out/replay/2026-11-05/`.
- **Outside the repo:** the DSS Post Review page shows each ticket's bot and developer time, in
  the ticket header, the real thread's outcome card, and a Results panel with medians.

### 2026-11-12 (later): a Confluence page's age counts against it (Vincent, with Claude)
- **Why:** Vincent: a page not edited within a year of the DSS post may be out of date; its last
  edit should weigh on how much the bot trusts it.
- **Done** (branch `trial-replay`, as `confluence.py` changed there):
  - `confluence_search` hits and `confluence_page` pages last edited over a year before the
    ticket carry `may_be_out_of_date` ("last edited 2025-06-30, 1.4 years before the ticket"):
    before now in a live triage, before the post in a replay (by the page's version then).
  - `triage.md` rule: check such a page against the code at the analyst's version, give its edit
    date when citing it, settle nothing on it alone; when page and code disagree, the code wins.
- **Checked:** `uv run pytest`: 261 passed. Live, code only: 3 of 50 hits for five typical searches
  were over a year old (1.0 to 1.4 years).
- **Not done:** CLAUDE.md asks for a few real triages before and after a `triage.md` change; not
  run yet (one costs about $0.30 to $0.70). Not committed.

### 2026-11-12: replays read the thread as raised, with the screenshot (Vincent, with Claude)
- **Why:** the workflow posts in three parts within about 2 s: "@<analyst> created DSS-…", its
  first reply "Screenshot of error:" with the analyst's screenshot, and a reminder (Data Support
  Request, After Hours Support Request, and AggBI Support Request for 3 tickets). Replays cut the
  thread at the post itself, so all six missed the screenshot. In 14 of 40 recent threads the
  analyst added more (often screenshots) before anyone else replied, from 7 s to about 19 min
  after. Vincent: always read "Screenshot of error:", and read the thread up to the first post by
  anyone but the analyst named in "@… created DSS-…".
- **Done** (branch `trial-replay`):
  - A replay's own thread: the post, then the workflow's messages and the analyst's, up to the
    first reply from anyone else and for at most 30 minutes (Vincent's cap: a later note by the
    analyst may say how it was solved; `thread._as_raised`). Other tickets' threads: what was
    posted by the cutoff. A replay no longer sets DSS_THREAD_UNTIL.
  - The replay prompt says to read the thread first (the screenshot is what the analyst saw) and
    to compare the project files with it and the description: differs and changed since, likely
    fixed there (answer as then, note the file with the value then and now in
    `replay.fixed_later`); differs but unchanged, nothing was fixed there, so the analyst may have
    looked at another file or run.
  - The live bot already reads the screenshot (its traces since DSS-1237 show the image sent), so
    its prompt is unchanged.
- **Checked:** `uv run pytest`: 259 passed. Live: DSS-1186's replay thread is the post, the
  screenshot and the reminder; DSS-1245's adds the analyst's 4 follow-ups (2 more screenshots) and
  stops before the first developer reply (28 messages in all); DSS-1229's runs to the analyst's
  last note 18 min after, with 7 images.
- **Not done:** the six replays aren't re-run with the screenshot, and this isn't committed yet.
  A stray `gitdss-bot-cerberusprod/` folder sits in the replay worktree; left alone.

### 2026-11-06 (afternoon): replay kept apart from regular runs, and up to date with main (Vincent, with Claude)
- **Why:** before merging, Vincent asked to confirm that trial mode (`replay`) is separate from the
  bot's regular runs, which may read every source, and to close what wasn't.
- **Done** (branch `trial-replay`):
  - Brought up to date with PR #13 (step-order gate, MATLAB CAT); the conflicts in `record.py`,
    `runner.py`, `server.py`, `conftest.py` and this file keep both sides.
  - In a replay the `matlab_*` tools read origin/master as of the post, by commit date (GitHub's
    push history is read for Cerberus only), and refuse when CAT had no commit by then.
  - A call held back by the step-order gate reads nothing, so it no longer counts toward a
    replay's 30 calls, nor in the calls the replay reports. A `jira_search` refused by a replay's
    JQL rules still counts as searched, as a failed search does live.
  - Only a replay runs in one: `.env` never sets DSS_REPLAY_AT, DSS_REPLAY_KEY or
    DSS_REPLAY_MAX_CALLS (`cutoff.SETTINGS`), and every command but `mcp` drops them at start,
    with a line on stderr.
- **Checked:** `uv run pytest`: 257 passed (5 new). Main's own tests (202) pass on this code, so
  regular runs are unchanged. `ruff check` lists six findings, all on lines main already has
  (`cerb.py:1318`, `code.py:195`, four in `eval.py`); left as they are.
- **Not done:** known causes carry no date, so one learned after a post from an older ticket
  still reaches that replay's prompt; replays share the Cerberus clone with the live bot, so two
  fetches at once can collide; a folder per replay run (proposed); `--go` renamed to `--dry-run`
  (proposed). The six replays aren't re-run on this code.

### 2026-11-06: DSS-1249's two failures, and MATLAB CAT as a reference (Vincent, with Claude)
- **Why:** the live bot posted two failures for DSS-1249 to the test channel:
  - ":warning: Could not triage DSS-1249: Claude failed: Expecting value: line 1 column 1".
    `docs/known_causes.json` had four stray characters typed before its `[` (saved Nov 5, never
    committed). The record prompt reads that file, so the triage stopped before Claude ran.
  - After the file was fixed and the bot restarted on `106dc63`, catch-up re-triaged it
    ($0.93). Claude searched Jira, then read code before any git history search, so the order
    check refused the answer and the post said "failed the record check".
- **Done** (branch `step-order-gate`):
  - `server.ReportingTools.waiting`: with `DSS_STEP_ORDER=1`, which the runner now sets, the
    reading tools (`record.INVESTIGATION_TOOLS`) answer "Not yet" until the run has called
    `jira_search` and a git history tool. A search that failed counts, so a source that is down
    never blocks the triage. The held-back call is traced as `blocked`, and `traced_tools`
    leaves it out of the order check. `triage.md` step 2 says so.
  - A JSON file the record prompt reads that is not valid JSON is named in the error
    (`record._json_file`). `triage.explain` says "The triage failed:" instead of "Claude
    failed:"; Claude's own failures already say `claude -p`.
  - New known cause `las-maxpen-not-peak-preserved` (DSS-1249): with a Depth Step, the
    Cerberus LAS resamples `idMaxPen` with `np.interp`. Checked on the ticket's project: the LAS
    equals the interpolated full-resolution curve to 4 decimals, so a 0.30 in pit reads 0.239 in.
    The MATLAB export (`cat_export_deliv_las.m` in DataAnalysis) has peak-preserved `idMaxPen`
    and `idMaxWL` since 2023-02-09, while its Confluence SOP says LAS resampling does not
    preserve peaks "and that's OK". DSS-1249 was resolved the same day: the LAS has no
    peak-preservation requirement (DTN-3747 cancelled). The known cause says so.
  - The record module's docstring no longer says the Slack bot does not use it.
  - The lesson from DSS-1249: whether a behaviour is intended needs both references, the
    Confluence SOP and the MATLAB CAT code Cerberus was ported from, and either can be wrong.
    Here the SOP says LAS resampling keeps no peaks "and that's OK", while CAT has kept the
    `idMaxPen` and `idMaxWL` peaks since 2023-02-09 (CAT 2.0).
    - New `dss/matlab.py`: `matlab_grep`, `matlab_read` and `matlab_log` read DataAnalysis
      through git at origin/master. The bot's own clone (`DSS_MATLAB_GIT`, made by
      `python -m dss cerberus` without files over 1 MB) is about 30 MB and is fetched before
      each triage. Paths are confined by `code._checked`; git reads only.
    - `matlab_read` is an investigation tool, so it waits for the past-issue searches too.
    - New rule in `triage.md`: when code alone can't say whether a behaviour is intended, check
      the SOP and CAT, quote each, and leave a disagreement to the developers under Not proved.
    - CLAUDE.md lists DataAnalysis as a read-only related repo; README and `.env.example`
      updated.
- **Checked:** 202 tests pass, 4 of them new. Ruff is clean on the changed files. The record
  prompt is 19,561 characters (limit 20,000). The MATLAB tools found the CAT peak preservation
  and its 2023 commit on the real clone. Live:
  - DSS-1249 re-triaged with the order gate ($0.58), posted to the test channel: it passed the
    check, searching Jira and git history before reading code with no call held back.
  - A trial with the MATLAB tools ($0.27): passed the check, read `cat_export_deliv_las.m` and
    the Cerberus LAS page, cited the Jira resolution, and left the restore decision to the
    developers.
- **Not done:**
  - The record prompt has about 400 characters left before the command-line limit; the next
    rule or known cause needs room made first.
  - Refused posts for schema problems remain possible (2 of 17 trials so far); a repair turn is
    an option.

### 2026-11-06 (morning): replay guards hardened after the first six replays (Vincent, with Claude)
- **Why:** the six replays of 2026-11-05 still saw later files (DSS-1163 a `Deliverables\DSS-1163`
  folder; DSS-1186's reply used the time a sheet was edited), and code's leak lines crowded the
  post. Vincent asked to close what can be closed and to report the rest.
- **Done** (branch `trial-replay`):
  - Nothing says *when* a hidden thing happened: `changed_after_ticket` is a flag without time or
    size, and refusals give no time.
  - Project share: changed files and any folder whose name says they were made later (the
    ticket's key, a later date) are left out of listings, and paths through such folders are
    refused (`cutoff.named_later`); projects dated after the ticket's month aren't found.
  - Jira: searches may test only fields a ticket keeps once filed, with absolute dates, sorted by
    created or key (`jira._steady`), so `status = Done`, `linkedIssues()` and `-30d` are refused;
    a cut changelog is read whole; a comment edited since shows no text.
  - Slack: a thread posted after the ticket is refused; a post edited since says so.
  - Confluence: a page with many versions is paged back to its version then.
  - Leak checks: later DSS keys count as leaks. The post says how many tool answers code flagged;
    the thread lists them.
  - Outside the repo: the DSS Post Review page's thread pane kept the previous ticket after a
    submit (Sean found it); fixed and republished.
- **Then, on Vincent's answers about the bottlenecks:**
  - Changed project files are read again, as they are now, and say so (`cerb.as_now`). When one
    shows the fix, Claude answers as the ticket stood (as if not fixed) and notes it in
    `replay.fixed_later` ("Fixed later in a file" in the post); this replaces `changed_file`
    (stop there).
  - Git as GitHub held it: GitHub's push history (activity API through `gh`, GET only; 9,066 rows,
    33 s the first time, then only what's new; `out/archive/github_activity.json`) gives each
    branch's commit at the post (`code.branches_then`). History searches walk those, branches
    list those, and `code_contains` refuses commits GitHub didn't hold then. Commit dates stand in
    when there is no push history.
  - Dated archive for searches: Jira hits edited since are kept only if their summary and
    description then have the searched words; Confluence hits changed since are judged, titled and
    excerpted by their version then, kept in `out/archive/confluence/`.
  - Prompt: lines naming the replayed ticket or a later one are left out; general rules stay.
  - Vincent: tickets from before telemetry keep "the release that was prod then" (no fix exists).
- **Checked:** `uv run pytest`: 246 passed. Live, code only: on DSS-1163's project the ticket's
  folder is left out (18 entries then, 19 today) and refused; Jira refused `status = Done`; at
  DSS-1191's post GitHub held 749 branches and 8,106 commits, including the same-day DTN-3633
  commit its replay doubted (so no leak); at DSS-1163's post that commit is refused; Jira and
  Confluence searches ran in about a second each.
- **Not done:** the six replays aren't re-run on this code. Still open: a ticket or page that
  matched a search then but no longer does isn't found, hits are ranked by today's text, and a
  file made later with a plain name is listed as changed (the index has no creation times).

### 2026-11-05 (evening): replay mode, old tickets as they stood when posted (Vincent, with Claude)
- **Why:** trials of old tickets read today's sources: Jira comments and Outcome, later commits,
  rewritten project files. Vincent set the rules: only the workflow's post, the poster's own build
  with git cut at the post, Jira as first created (never the ticket's comments), project files only
  as saved before, a cost cap, every leak named in the post, no remembered context, no wheel builds.
- **Done** (branch `trial-replay`): `python -m dss replay <keys> [--golden] [--go] [--post-test]`
  (`dss/replay.py`), and `dss/cutoff.py`, which every tool reads (DSS_REPLAY_AT, the post's ts):
  - Slack: the post only. Jira: rebuilt from the changelog, no comments on the ticket itself,
    later tickets refused, `text ~` kept to summary and description. Confluence: the page version
    of then. Git: `--before` the post, branches as they were, later commits and releases refused;
    `cerb_client_info` runs the analyst's build exported with `git archive`. Project share: later
    sessions and log lines cut; files changed since are listed as changed and refused, and Claude
    stops there (`replay.changed_file`; replaced on 2026-11-06 by reading them as they are now,
    above). Known causes learned from this ticket or later dropped.
  - Prod then (Vincent's choice): a release counts from the latest of its release commit, the day
    after its Jira release date and, for today's wheel, its file time. Release commits come days
    early (1.0.4: commit Oct 9, Jira Oct 13), and 1.1's wheel reached prod at 15:23 on Nov 4, two
    minutes after DSS-1247, so commit dates alone were wrong.
  - Caps (Vincent's choice): 30 tool calls, then the tools say "answer now"; `--max-budget-usd 1.50`.
  - Leaks: code checks each tool answer for the ticket's key and later dates; Claude lists what
    looked later. Both go in the record's new optional `replay` block and in the post.
  - `slack.post` holds the posting the bot and `--post-test` share; it refuses #data-dev-support.
- **Checked:** `uv run pytest`: 219 passed (20 new, all sources faked). Live, code only: Jira
  rebuilds (DSS-1101 and DSS-1229: Done/Bug/toolbox and comments back to To Do with none), search
  finds only older tickets, git cut at the post. Six real replays for the DSS Post Review
  (DSS-1081, 1086, 1095, 1163, 1186, 1191): $2.49 together, 9-29 tool calls each, outputs in
  `out/replay/2026-11-05/`. Fixed after them: the post's own due dates were flagged as leaks,
  leak lines were double-escaped, a cut session log looked like a leak to Claude, and Jira's
  "released <date>" on fix versions is no longer shown in replays.
- **Not done:** listings still show when a file changed and files made after the post (DSS-1163
  saw a folder named after the ticket); code-found leak lines make the post long; `dss eval`
  doesn't score replay records yet. Two answers (DSS-1086, DSS-1095) were refused by the live
  rule that git history is searched before the investigation.

### 2026-11-05 (later): Cerberus's terminal log as a source (Vincent, with Claude)
- **Why:** from Cerberus 1.1 (`ab952f341`, DTN-3710), each session writes loguru's DEBUG output
  to `<project>\cerberus\terminal_log\<login>_terminal_log_<start>_<pid>.log`, named like its
  telemetry log. The bot can read the error text itself instead of asking the analyst. Cerberus
  1.0.4, and the UAT build `1.0.4+uat20261031`, write none.
- **Done** (branch `terminal-log`):
  - `cerb.last_session` also reads the terminal log paired with the session
    (`cerb.terminal_log`, `cerb.log_problems`):
    - Only lines before the ticket time are read.
    - Each distinct ERROR or CRITICAL is listed once, at its newest line, with how often it fired.
      It keeps the end of its traceback (its deepest frames and the exception) or its banner's
      lines, without the variable values loguru prints.
    - Each WARNING call site is listed once, at its first line.
    - Every item has its line number for `cerb_read_text`.
  - With no log: "Cerberus 1.0.4 writes no terminal log; Cerberus 1.1 and later do", or why a
    1.1 session's log wasn't read. Only the second becomes a note for the reviewer.
  - The record's `dss_ticket` block has a new `terminal_log` field (schema, example), so Claude
    gets the errors without a tool call.
  - `triage.md`:
    - Step 1 reads the last session.
    - Gates B and C and the crash playbook look in the terminal log first.
    - Claude asks for the error text only for sessions before 1.1, or when the log shows no error
      for the problem.
  - `triage_record.md`: the new fact, and `terminal_log:line <n>` as an evidence ref.
  - README updated.
- **Checked:**
  - 198 tests pass, 7 of them new.
  - Ruff is clean on the changed files, apart from the old SIM108 in `cerb_client_info`.
  - mypy reports the same 13 older errors in `cerb.py`.
  - Live, code only, on the terminal logs of two recent DSS tickets' projects. A 716 KB log
    became about 4,000 characters, including a CATCALL assertion with its frames and a
    PermissionError while saving the defect summary.
  - DSS-1247's facts at its post time: Cerberus 1.0.4, no log, no note. At a later time: the
    reporter's 1.1.0.68 session, with 1 error and 7 warning call sites, passing the schema.
- **Not done:**
  - No Claude trial: no DSS ticket yet comes after a 1.1 session.
  - Not committed. The live bot needs a restart after the merge.
  - The DSS Triage Flow page still lists the terminal log as planned.

### 2026-11-05: a shorter thread under the post (Vincent, with Claude)
- **Decided by Vincent**, after Annika's and Sean's comments:
  - The thread keeps Why, Past issues, Evidence, What the questions settle, Details, Also found
    and Confidence.
  - The bot writes no DTN drafts, since the team writes DTN tickets with its own skill.
  - The class, the labels, the existing DTN ticket (`escalate`) and the facts code read leave
    the thread but stay in the saved record.
- **Done** (branch `thread-sections`):
  - `record._proof` renders the seven sections:
    - Claude's flags and code's notes fold into the Confidence line as "could not read", each
      said once.
    - Also found shows only the cause.
    - Confidence reads "high, settled at the wrong numbers playbook".
  - A past issue's text now ends in a full stop before the reason it applies; the two ran
    together.
  - `_planning`, `_labels` and `_facts` are removed, and so is `_request`, which nothing called.
  - `escalate` takes only an existing DTN ticket: the schema refuses a `draft`, and
    `triage_record.md` says to write none. The developers' "raise a DTN ticket" todo no longer
    points to a draft.
  - README: the thread as a table.
- **Token cost of these changes** (16 saved trials, 17 logged runs since 2026-10-30):
  - The thread changes cost nothing either way: code renders the thread after Claude answers.
  - 2 of the 16 trials wrote a DTN draft (517 and 903 characters, about 130 to 230 tokens). A
    run's median output is 6,847 tokens, so dropping drafts saves well under 1% of output.
  - Most of a run's cost is reading: the cached context is read again on every turn (median
    about 225k cache-read tokens; 1.2M on a 58-turn run). Fewer turns is the lever for cost.
- **Checked:**
  - 191 tests pass, one of them new.
  - Ruff is clean on the changed files.
  - mypy is clean on `record.py`; the 13 errors it reports are all in `cerb.py`.
  - The example record was rendered by hand.
  - No live trial: the prompt change is one phrase.
- **Not done:**
  - Not committed. The live bot runs `main` from the main checkout and needs a restart after the
    merge.
  - From Annika: Jira's comments, outcome, status and links leak the answer into trials of older
    DSS tickets. Only the Slack thread is cut at the post. Next: an as-posted Jira built from the
    change history.
  - The DSS Triage Flow page and the Reply Quality Tracker still describe the older format.

### 2026-11-04 (night): todo titles, links and a shorter footer (Vincent, with Claude)
- **Asked by Vincent:**
  - number the todos 1., 2. (Slack letters a list inside a bullet a., b.);
  - drop `dss-triage/3` and "not posted in the DSS thread · the DSS post" from the footer;
  - link DSS and DTN tickets and GitHub commits, branches, PRs and releases, with no previews
    except the DSS post's;
  - replace Actions with "Bot Summary", "<analyst's Slack name> Todos", "Data Dev Todos" and
    "Draft Reply", leaving out a todo list with nothing in it.
- **Done** (branch `source-gaps`):
  - Each todo list is a top-level numbered list under a bold title. The analyst's title is their
    Slack display name as plain text: a mention could notify them of a draft. The Jira reporter
    stands in without Slack (the `trial` CLI). No `'s`, as many names end in s.
  - The headline's key links to the DSS post, which gives the snapshot below the post. The
    footer is "Draft by Claude · tokens · cost · time".
  - `code.web_links` finds commits (checked with `rev-parse`), remote branches (a branch's PR
    when GitHub's pull refs, read with `ls-remote`, name its tip), "Cerberus x.y" releases (the
    release commit's PR) and "PR #N" or "(#N)". `master` is not linked. A failed lookup becomes a
    note, and the post goes out with Jira links only.
  - The Draft Reply links only Jira tickets: analysts cannot open GitHub.
  - `unfurl_links` stays at Slack's default. `False` also hid the DSS post's preview (tried in the
    test channel); Jira and GitHub links show no preview at the default. `unfurl_media` is off.
- **Checked:**
  - 190 tests pass; ruff is clean apart from the old SIM108s, FURB167s and SIM110.
  - DSS-1247 re-posted in the new format (16:15): the DSS snapshot shows, nothing else unfurls.
  - The links of DSS-1247 found in 1.2 s; the DTN-3726 branch links to PR #1018 and
    `develop-v1.1-release` to the 1.1 release PR #978.
  - A test run of DSS-1237 (posted 2026-10-31; 58 turns, 181 s, $1.39) in the new format. Its
    verdict leaned on a rerun log a developer put on the share after the ticket: the thread is
    cut at the post, but the share and Jira are read as they are now.
- **Live:** the bot restarted at 16:44 with the new format, still posting to
  #data-dev-support-assist-test.
- **Not done:**
  - "As posted" trials, which DSS-1237 showed matter for replaying old tickets.
  - A logon task.
  - Annika's OK on the destination, then `DSS_SLACK_POST_CHANNEL` back to #data-dev-support-assist.

### 2026-11-04 (evening): a shorter post, agreed with Annika (Vincent, with Claude)
- **Feedback from Vincent and Annika on the first posts:**
  - say each thing once;
  - give analysts the answer, not the internals;
  - "Reported" and "Request" were the same;
  - name exact data, not "here";
  - don't state what the reader can tell already;
  - combine the actions and the notes (Annika).
- **Done** (branch `source-gaps`; record `dss-triage/3`):
  - The post: a headline with the verdict in a few words, then Summary.
  - Then Actions for the analyst and for the developers. Questions become "Answer" actions;
    what is not proved becomes "Confirm" checks.
  - The Reply comes last: at most 3 sentences, solution first, exact facts. No code, functions
    or line numbers, and no release unless a DTN fix version names it. The check refuses `.py`,
    `cerberus/` paths and line numbers in it.
  - The thread: why, past issues, evidence, DTN draft, confidence, class.
- **Also fixed:** DSS-1242's first trial lost its answer to an `AttributeError` in the trace
  check, on a malformed `triage` block. That check now tolerates it, and any error while checking
  or rendering becomes a listed problem, with the traceback and the answer saved.
- **Checked:**
  - 185 tests pass; ruff is clean; the system prompt is 17,786 characters.
  - The DSS-1242 trial (`dss-triage/2`, likely, $0.40) was laid out by hand in the new format to
    agree it.
- **Live:** the bot restarted, posting to #data-dev-support-assist-test (`DSS_SLACK_POST_CHANNEL`
  set to the test channel in `.env`) until the format is checked on new tickets.
  - Its first live ticket, DSS-1247 (44 s, $0.23): known issue DTN-3726, fixed in 1.1.
  - The run exposed a gap: Cerberus 1.1 went to prod today as `cerberus-1.1.0.68`, but the
    release commit is `release: 1.1 (#978)`, so `prod_ref` found nothing.
    - The lookup now also tries the version without the build number and without a zero patch.
    - All 444 files of the 1.1.0.68 wheel match `ab952f341`.
  - Actions now render as bullets (Analyst, Developers), with a numbered list inside. Slack
    letters the inner list a., b.
  - The bot restarted with both changes; 187 tests pass.
- **Not done:**
  - "As posted" trials: hide sources newer than the post, so old tickets don't find later fixes.
  - The PR lookup from git pull refs, with links (done that night).
  - Merging `source-gaps`.
  - A logon task.
  - Annika's OK on the destination.

### 2026-11-04 (late afternoon): the bot goes live in the developers' channel (Vincent, with Claude)
- **Decided by Vincent:**
  1. Triage 15 s after the post (the workflow's screenshot lands within a second).
  2. The live bot uses the checked record.
  3. Analyses go to #data-dev-support-assist; new features and debugging go to
     #data-dev-support-assist-test; nothing goes to #data-dev-support until the replies are
     consistently good.
- **Done** (branch `source-gaps`):
  - `SlackDesk` runs `record.trial` and posts the decision to `DSS_SLACK_POST_CHANNEL`, with the
    proof in its thread, as Markdown blocks. Without that setting it posts to the reviewer's DM.
  - It refuses #data-dev-support, or the watched channel, as a destination, at start and on every
    post.
  - A blank line now ends each list in the DM and thread, so "Evidence" numbers from 1 (Slack had
    run on from "Past issues").
  - CLAUDE.md, README and `.env.example` updated.
- **Live:**
  - `ddev-support-bot` has Socket Mode, an app-level token and the message events (set up by
    Vincent as a collaborator).
  - The bot started on Vincent's PC about 14:00 and connected. DSS-1245 and DSS-1246 were marked
    done first, so it starts with the next new ticket.
  - It runs as a background task of a Claude Code session, so it stops when that session ends,
    until a logon task runs it.
- **Checked:**
  - 176 tests pass; ruff is clean on the changed files.
  - One Markdown-block post to the test channel rendered bold, code and escapes correctly.
- **Not done:**
  - Merge `source-gaps`.
  - A Windows logon task, so the bot survives the session.
  - Annika's OK on the destination change (CLAUDE.md says both owners).
  - The first live ticket end to end.

### 2026-11-04 (evening): the sources the bot could not see (Vincent, with Claude)
- **Why:** a live probe of every source (Jira, Confluence, project index, telemetry, git, Slack)
  found all of them up. What the tools could not see:
  - Jira comments and fix versions;
  - PDF and Word files;
  - images on the share;
  - files over 50 MB;
  - data files.
- **Done** (branch `source-gaps`, one commit each):
  1. `jira_get_issue` adds:
     - resolution;
     - fix versions, released or not, with dates;
     - the newest 10 comments;
     - attachment names.

     `jira_search` adds resolution and fix versions.
  2. `cerb_read_document`: the text of `.pdf` and `.docx`, page by page (pypdf and python-docx
     in the Cerberus venv).
  3. `cerb_read_image`: images on the share, and the pictures embedded in a PDF page. Scaled to
     1568 px, logos left out.
  4. Copies stream to disk up to `DV_PROJECT_INDEX_MAX_MB` (default 1 GB; Vincent to confirm),
     are reused for 15 minutes, and are deleted after a day.
  5. `cerb_read_table` (Parquet; SQLite read-only) and `cerb_read_caliper` (`.dvcaliper` and
     caliper `.npy`, by the prod Cerberus's reader).
     - A `.dvcaliper` window is read by HTTP ranges, so a 4 GB file is never copied: header,
       binary search on depth, then only the window.
     - Pickles and `Thumbs.db` are refused.
- **Checked:**
  - 177 tests pass. Ruff is clean apart from the old SIM108.
  - Live:
    - DTN-3726 shows its fix version (CERBERUS v1.1, not released) and the developer's comment.
    - The 2D log intro pages PDF: its text, and page 2's overview plot as an image, with the
      repeated "0.2".
    - A 496 MB Word file and a 282 MB PDF (943 pages) copied in 2-4 s and reused.
    - A Parquet file of 1,451 rows x 155 columns.
    - DSS-1241's 4 GB wall-loss caliper: summary in 2 s, and the window at 15026 ft in 3 s.
      It shows the breach (0.007722 m = 0.304 in) at 15026.25 ft, inside a ring of shallower
      loss around most of the pipe.
  - All 14 `.db` files in three projects were `Thumbs.db`.
- **Not done:**
  - Not pushed.
  - Open PR titles (needs a GitHub token), other Slack channels (invites), UAT builds pushed.
  - Word files' embedded pictures.
  - Jira attachment downloads (the download redirects to another host with the login).

### 2026-11-04 (afternoon): Slack thread live, and the bot's own Cerberus (Vincent, with Claude)
- **Merged:** PR #8 (`trial-followups`: threads and screenshots, workbooks, past issues first).
- **Slack:**
  - `.env` here has the bot token of Annika's `ddev-support-bot` app, and no app token, so no
    live bot can start on this PC.
  - The bot was invited to #data-dev-support. These reads worked:
    - the channel's history;
    - the threads of DSS-1240 to DSS-1245, with 10 screenshots downloaded (one checked by eye).
  - One test post to #data-dev-support-assist-test, Vincent's test channel, at his request.
    Nothing was written to #data-dev-support.
  - The app lacks `channels:read` (the bot can't list its channels; not needed).
- **The bot's Cerberus** (branch `cerberus-prod-copy`, at Vincent's request):
  - `DSS_CERB_GIT`: the bot's own clone. `code_*` read it, and each triage fetches every branch
    first (`code.refresh`). Unset, they read the developer checkout as before.
  - `DSS_CERB_PROD_CODE`: the prod wheel's Cerberus is unpacked there, one folder per version.
    `cerb_client_info` imports it first, so it runs the Cerberus analysts installed, not the
    checkout's branch. The venv still provides the dependencies; installing the wheel's 62
    (GPU, Qt, two internal packages) was not worth it.
  - A failed fetch or unpack is told to Claude as a note and doesn't stop the triage.
  - `python -m dss cerberus` sets both up and says what each tool reads.
  - Found while checking it live: `python -c` puts its working folder (the checkout) ahead of
    `PYTHONPATH`. The worker now puts the prod folder first itself, and the tests use a decoy
    checkout.
  - `.gitignore` now also covers `.env.*` and `*.swp`: an editor swap file of `.env` had
    appeared, holding the tokens.
- **Checked:**
  - 163 tests pass; ruff clean on the changed files apart from the two old SIM108s.
  - Live on this PC (`F:\git\dss-bot-cerberus`):
    - the clone and a fetch;
    - prod 1.0.4 = `ff8a0091e`;
    - `cerb_client_info` loads from `prod\1.0.4\cerberus`;
    - two real projects' client info (19 and 292 joints) parse identically with the prod copy
      and the 1.1.0 checkout.
- **Not done:**
  - Not committed.
  - The live listener: an app token, Socket Mode and `message.groups` events on Annika's app,
    agreed with her so only one listener runs.
  - UAT builds must be pushed for the bot's clone to see them.
  - Still open: the triage delay (5 min vs about 15 s), and the first trial with `--thread`.

### 2026-11-04: trial on DSS-1243 and a demo of visible investigations (Vincent, with Claude)
- **Checked:**
  - A trial on DSS-1243 (two "0.2 in" labels on the eccentricity scale), from the post only. It
    took 68 s and $0.38, passed the record check, and answered likely, medium. It found the
    cause in `techlog/intro_pages.py` (`MaxNLocator(nbins=7)` plus `'%.1f'`), the duplicate
    DSS-1232 and the unreleased fix DTN-3726 (`2ea08f0ea`). It asked for a screenshot that was
    already in the thread.
  - The session proved the rest:
    - Cerberus's matplotlib 3.10.8 (prod's pin) reproduces the screenshot's labels on a 0 to
      0.5 in axis: ticks 0.08 apart, with 0.16 and 0.24 both printing 0.2.
    - The fix gives distinct labels.
    - DTN-3726 is Done, with fix version CERBERUS v1.1 (not released).
    - The UAT build `e7d6f928c` (1.0.4+uat20261031) lacks the fix.
  - The session read the developer's replies by mistake before checking: a `slack_read_thread`
    call with `limit` returns the newest replies, not the first ones.
- **Done:**
  - A demo page for making investigations visible: steps, evidence-graded confidence, hypothesis
    curves, mistakes and a reviewer's "Flag a mistake" button. It is a private artifact, built
    from one JSON block.
  - The page proposes four changes:
    - `hypotheses` and `steps` fields in the record;
    - code-set score bands checked against the trace;
    - `python -m dss report`;
    - an outcome and corrections log.
- **Done (later the same day), at Vincent's request: past issues first.**
  - `triage.md` now runs every ticket as:
    1. state the request;
    2. search past DSS and DTN tickets, git history and branches, and Confluence;
    3. check whether a past fix applies, including whether it is in the analyst's build;
    4. only then the gates and playbooks.
  - Stopping rule: "likely" only when no tool can read the last link. The "one `jira_search`"
    rule is now up to three.
  - New git-read tools in `dss/code.py`:
    - `code_log` (recent commits on a path);
    - `code_branches` (remote branch names);
    - `code_contains` (does prod, a release, a UAT build or a remote branch include a commit or
      branch).
  - Record `dss-triage/2`:
    - required `request` and `prior_art`;
    - `settled_at` gains `prior_art`, which needs an item that applies, and `record` is renamed
      `request`.
  - Enforcement:
    - every trial now keeps its trace;
    - the record check refuses a run that read code or project files before a `jira_search`
      and a git history search (except `not_cerberus`).
  - The DM shows the request; the thread lists past issues.
- **Checked:**
  - Tests: 156 pass. Ruff is clean on the changed files apart from the old SIM108 in
    `code.py`'s `grep`, left as a block.
  - Live `code_contains` and `code_branches` against the real checkout.
  - Re-ran DSS-1243, DSS-1240 and DSS-1235 from the post only ($1.57 in total). All three passed
    the check and settled at past issues.
    - DSS-1243: reached DTN-3726 at call 3 (was 11). It checked the UAT build and offered the
      rerun with the fix, but went to low confidence because the plot was unseen.
    - DSS-1235: found Annika's `DSS-1210-work-annika-raid-headers`, which the old run missed. It
      cost $0.90 (was $0.39): 4 of its calls failed on branch names, so `code_contains` now takes
      branch names.
    - DSS-1240: proved, but DTN-3741 was created after the old run, so it is not a fair
      comparison.
  - Comparison page: a private artifact, built from the traces by a scratch script.
- **Not done:**
  - Nothing committed.
  - The production bot's Markdown answer follows the new order only through the prompt; nothing
    checks it there.
  - Cherry-picked fixes are invisible to `code_contains`.
  - No tool for open PR titles (needs a GitHub token; both owners).
  - Annika to review the new order and the `dss-triage/2` schema. The DSS Triage Flow page
    still describes `dss-triage/1`.

### 2026-11-03: trial on DSS-1240 (Vincent, with Claude)
- **Checked:**
  - A trial on DSS-1240 (the reference perforation plots combine all calibrations), from the
    post only: this PC has no bot token, so `--thread` couldn't be used. It took 103 s and
    $0.68 and failed the record check: `escalate.kind` came as `draft`.
    - Its reading went further than earlier trials. It used `cerb_read_sheet` on Stage Designs
      and the baseline calculation, and read the plot's SVG as text.
    - It called the ticket a likely feature request (one figure for all stages). It asked whether
      the spec lines sit at different heights, which it couldn't see.
  - Against the screenshots and the project's files, read by the session:
    - Both delivered reference plots have one spec, median and ±1 SD line across all 84
      reference perforations: each SVG LineCollection holds one segment spanning the axis. The
      spec line is 0.42 in for all three charges (0.38, 0.40 and 0.42 in).
    - Since DTN-3214 (b7970ca41, in release 0.14.1) the plot groups its lines by `stage`.
      Before that it grouped them by charge type (395cd1003).
    - Cerberus's `stage` is Perf Info's `dvStage`, which is 69 for all reference perforations,
      while their client stages are 69, 70 and 71. Cerberus's saved calibration table has
      stage 69 for all 84, so the plot draws one group. The document's "one stage" comes from
      the same column.
  - `cerb_read_sheet` now refuses an Excel lock file (`~$…`) with a readable message, with a
    test. `uv run pytest` (143 passed).
  - A trial on DSS-1241 (two tiny defects reported as one large one), from the post only: 74 s,
    $0.53, record check passed, "needs info, low confidence".
    - It found the interaction rule (3 × 0.304 in nominal wall) and the 12.5 % threshold, and
      named the 0°/360° seam handling as a possible bug.
    - Without the thread it asked the analyst for the depth and the two defects' positions,
      which the screenshots show, and it guessed the wrong defect row.
    - The session found the row in the delivered Defect Summary: 3.48 in long and 13.32 in wide
      at 100 % wall loss. On that joint's 4.892 in ID, 13.32 in is 312° of the circumference.
      Whether a band of shallow wall loss joins the two patches or the seam handling measures
      the long way round was not settled.
- **Not done:**
  - Whether the plot should group by stage and charge type, or the project's `dvStage` should
    differ, is for the Cerberus owners.
  - The trial prompt's `escalate` wording (`kind` plus `draft`) was misread again, after
    DSS-1234's shape errors.

### 2026-11-02: the triage reads the DSS ticket's Slack thread and screenshots (Vincent, with Claude)
- **Why:** the DSS-1236 and DSS-1239 trials were wrong or noisy because Claude saw only the
  workflow's post. Both threads already held the deciding screenshots when the trials ran, and
  the DSS-1236 trial asked the analyst for exactly those. Vincent's priority: a proved answer
  first; spending many tokens on a complicated ticket is fine, waste is not.
- **Changed:**
  - New read-only tool `slack_thread(ts)` (`dss/thread.py`). It returns the post and every reply,
    oldest first, with each screenshot as an image and each text file as text; other files are
    named.
    - It reads only threads of a workflow's "created DSS-…" post in the watched channel, with GET
      requests: `conversations.replies`, `users.info`, and the files.
    - The bot token is sent only to `https://files.slack.com`, and redirects are refused. A file
      that can't be read is named with the reason; the rest of the thread still comes back.
    - Limits: 200 messages, 20 images, 20 MB per file, 20,000 characters per text file.
  - The bot waits until 5 minutes after the post before it triages (`TRIAGE_DELAY_S`). It passes
    the post's ts, and the prompt tells Claude to call `slack_thread` first.
  - `triage` and `trial` take `--thread <permalink or ts>`. `--posted` then defaults to the post's
    time, and replies more than 5 minutes after the post are left out (`DSS_THREAD_UNTIL`), so a
    trial of an older ticket stays blind to the developers' answers. Trial files save
    `thread_ts`.
  - `triage.md`:
    - Puts a proved answer before saving calls.
    - Reads every screenshot and quotes it.
    - Starts a crash from the traceback's deepest Cerberus frame, then the input it reads.
    - Never asks the analyst for what the thread already has.
    - Keeps confidence low when Claude could not see the symptom itself.
    - Drops "Read little" and "You cannot open screenshots".
  - `triage_record.md`: `slack_thread` is an evidence ref.
  - `slack_manifest.yaml`: the `files:read` scope.
  - `SlackDesk`'s name lookup moved to `slack.Names`, shared with the tool. `slack._channel` is
    now `slack.watched_channel`.
  - New read-only tool `cerb_read_sheet(path, sheet, first_row, contains)` (`dss/cerb.py`) for
    any `.xlsx` or `.xlsm` on the share. The project index already fetched these files;
    `cerb_read_text` refused them as binary.
    - It copies the workbook through the index (`local_copy`, share paths only) and reads it with
      openpyxl in the Cerberus venv (`cerb.workbook`). Nothing is added to this repo.
    - Without a sheet it lists the sheets and their sizes. With one it returns up to 100 rows or
      30,000 characters, by Excel's row numbers, cells tab-separated, formulas as last saved, and
      where the next page starts.
    - `contains` keeps only the rows with a cell holding that text. A filtered or later page also
      shows the top 3 rows, for the column names.
    - The old binary `.xls` is refused.
  - `triage.md` names `cerb_read_sheet` in the crash and wrong-numbers playbooks.
- **Checked:**
  - `uv run pytest` (143 passed). Ruff check and format pass on the changed files, apart from one
    older SIM108 in `cerb.py` (`cerb_client_info`'s if/else, kept as a block on purpose). Ruff
    also flags `code.py`, `eval.py` and `test_eval.py`'s formatting, which this session didn't
    touch.
  - One live Haiku run (outside the `dss` server, $0.07) with the runner's locked-down flags and a
    made-up 6.4 MB PNG returned by an MCP tool. The image reached Claude and its text was copied
    exactly, at about 2.8k tokens, so screenshots need no resizing here.
  - `cerb.read_sheet` live on DSS-1239's project (code only, no Claude run):
    - It listed the Perforation Statistics workbook's 4 sheets in 1.1 s, then returned pages of
      the 1,451-row sheet in about 0.6 s each.
    - `contains="7x4"` returned the design's 28 perforation rows, the count found by hand on
      2026-11-02.
    - It listed the client info's 20 sheets.
- **Not done:**
  - `slack_thread` has not run against real Slack. This PC has no bot token, and the production
    app lacks `files:read` until the scope is added and the app reinstalled.
  - No live triage with the new prompt yet. Next: re-run DSS-1235, 1236 and 1239 with `--thread`
    and compare with the known answers (about $0.50 each).
  - Annika to agree to the 5-minute delay and the new scope.
  - No Claude run has used `cerb_read_sheet` yet; the DSS-1239 re-run will show whether it
    finds the design with no measured perforations.

### 2026-11-02: trial on DSS-1239 (Vincent, with Claude)
- **Checked:**
  - A blind trial on DSS-1239, where making the PE analysis document fails. It took 51 s and
    $0.25, passed the record check as "needs info, low confidence", and was sent to Vincent's
    DM.
  - Against the screenshot in the DSS ticket's thread, which the session read and the run can't:
    - The traceback is `ValueError: arange: cannot compute length` in the completions histogram
      (`histogram_plots.py` `base_histogram`) for one stage design.
    - That design's only stage has no measured perforations (all plugged, debris or no data),
      so the bin range is NaN. Calling `base_histogram` on the project's exported statistics
      reproduced the error for that design; designs with even one volume pass.
    - Prod, master and `develop-v1.1-release` have no guard for this.
    - The run asked the analyst for the error text the screenshot already shows, and followed
      the analyst's guess about a failed plug.
  - The low status was right. The gap is the screenshot again: all three trials since DSS-1235
    needed it.
  - The same screenshot shows Cerberus's telemetry sink failing on flush
    (`OSError: [Errno 22]`), which may be why the run found no telemetry for the failed
    session; not checked.
- **Not done:** DSS-1237 and DSS-1238 got no trial (Vincent picked the newest). Reading the
  workflow's screenshot is still open (see 2026-10-31).

### 2026-10-31: first trial on a new DSS ticket (Vincent, with Claude)
- **Changed:** the trial DM leaves a blank line after the escalation draft, so Slack no longer
  pulls "Suggested reply" into the quote (`record._decision`, with a test).
- **Checked:**
  - A blind trial on DSS-1235 (an overlapping RAID 2D Log header), 15 minutes after it was
    posted, from the workflow post only. The record check passed in 62 s for $0.39. The result
    was sent to Vincent's DM, with the proof in the thread.
  - Against the data developers' answer in Slack (a known header font problem, fixed on
    Arsenii's and Annika's branches, not released):
    - It found the cause, Arsenii's fix (d99479197, only on `DTN3703-work-ars-killer-2dlog`),
      that prod 1.0.4 lacks it, and the earlier DSS-1210.
    - It missed Annika's fix (1552cf587, only on `DSS-1210-work-annika-raid-headers`). That
      commit changes only label alignment, so the `code_log_s` text searches can't find it,
      and no tool lists branches by name.
    - Its workaround (wider tracks in the RADX layout) was not checked. A data developer said
      the font size is not a setting.
    - Neither fix is on `develop-v1.1-release`.
  - A blind trial on DSS-1236 (a Techlog 2D log missing the white line between joints), 61 s
    and $0.49, was wrong. It said "likely, medium confidence: casing sections, mark casingSplit".
    A data developer called it a bug. The screenshots, which the run can't see, show the line
    missing at a collar in the column beside the depth numbers, between two joints with defects.
    The run had flagged the white line as not proved, yet still rated its answer likely and
    gave the analyst an action.
  - `uv run pytest` (122 passed); ruff check and format pass on the changed files.
- **Not done:**
  - The run can't see the workflow's screenshot, the key evidence in both trials. Passing it to
    Claude needs the Slack app to read files; for the owners to decide.
  - A prompt rule that an unconfirmed symptom caps the status below "likely", to test on a
    few tickets first.
  - A read-only tool that lists remote branches whose names mention a DSS or DTN key would
    have found Annika's fix; for the owners to decide.
  - `known_causes.json` has no entry for this header problem.

### 2026-10-30: trial mode for the triage record (Vincent, with Claude)
- **Changed:** `python -m dss trial <key>` runs the triage record design by hand; the bot is
  unchanged. It takes the same arguments as `triage`. It runs the same locked-down Claude with
  `triage.md`'s steps and rules, but with `dss/triage_record.md` as the answer section.
  - Code first reads the `dss_ticket` block: Jira, the project folder, the workflow from the
    folder name, the reporter's last session, and the prod version. A failed source is noted
    rather than fatal.
  - Code checks Claude's JSON against the schema and renders a DM (the decision) and a thread
    reply (the proof). Claude's text is escaped so paths and names show as written.
  - Each trial is saved in `out/trial/`, never in `out/answers/`, which the bot reads.
  - Small changes, with no change for the bot: `runner` takes an optional system prompt;
    `jira.get_issue` takes optional fields; `triage.run` is split out of `triage.triage`; the
    CLI shares its ticket arguments.
  - `jsonschema` is now a runtime dependency.
  - Schema: an optional `project` field in `dss_ticket` (the page lists it as an input).
    `class.cause_family`, `feature` and `prevent` may be null, so a needs-info answer isn't
    pushed to guess them; this is for the owners to confirm.
- **Checked:**
  - `uv run pytest` (121 passed); mypy clean apart from the Cerberus import; ruff check and
    format pass on the changed files.
  - Live trials on DSS-1234, the newest DSS ticket: about 70 s and $0.35–0.38 each.
    - Run 1 failed the record check: `not_proved` items came as objects and `secondary.class` as
      one value. The prompt now spells out both shapes.
    - Run 2 passed. It found the known cause `path-too-long` (DTN-3727) and was sent to
      Vincent's DM through the Slack connector.
    - Two rendering faults in Slack Markdown (eaten backslashes, `__init__` as bold) and a footer
      joined to the quoted reply were fixed afterwards. The thread reply went out fixed; the DM
      had already gone out.
  - Merged as PR #7, which included #6. On `main` after the merge: 121 tests pass, and code read
    DSS-1233's `dss_ticket` facts from every source with no failure notes (no Claude run).
- **Not done:**
  - The bot posting the trial itself: this PC has no Slack bot tokens, so the session delivers
    the DM.
  - A repeat between Claude's `flags` and code's notes when both say the same thing.
  - Jira shows today's state: a DSS ticket two days old already showed its DTN link, so the run
    saw the answer. Not a problem for new DSS tickets.

### 2026-10-29: the triage record proposal as files; its planning fields tested on 45 DSS tickets (Vincent, with Claude)
- **Changed (a proposal; the bot does not use these yet):**
  - `docs/triage_record.schema.json`: the triage record from the DSS Triage Flow page, as JSON
    Schema. `dss_ticket` is filled by code, `triage` and `class` by Claude, `resolved` by a person.
    It checks the status rules:
    - proved and likely need a cause, evidence and `do_now`, and likely also needs `not_proved`;
    - needs info needs questions;
    - not Cerberus needs an owner;
    - proved wrong numbers needs both values and the first difference.
  - `docs/triage_record.example.json`: the page's example, with made-up data.
  - `docs/known_causes.json`: 17 known causes drafted from settled DSS tickets. Each gives how to
    prove the cause and the fix; 10 have two or more DSS tickets.
  - `tests/test_triage_record.py`. `jsonschema` is now a dev dependency; it was already installed
    through `mcp`.
- **Checked:** `uv run pytest` (97 passed); mypy clean apart from the Cerberus import. Each case
  the tests expect the schema to refuse was checked to fail for its intended reason. `ruff check`
  and `ruff format --check` pass on the changed files. On the rest of the repo, ruff 0.17.1
  reports 9 lint findings and 3 unformatted test files, all already on `main`; left alone here.
- **Tested: the planning fields on 45 more closed Cerberus DSS tickets.** They are a fixed-seed
  sample of the "ready" DSS tickets not sorted before. Claude sorted them from the Jira text; the
  result is in `out/eval/class_test.jsonl`.
  - The cause families held: 42 of 45 fit, and the other 3 have no information in Jira.
  - The first `feature` list did not: only 17 of 45 fit. The rest would only fit by lumping four
    PE deliverables into `pe_plots_tables`, and catcall, LAS export, the AIA and burst reports
    and tubeviews were missing. The schema has a revised list of 19 values, prefixed by workflow.
  - 14 of 45 have no resolution text in Jira, and none states the impact. A back-fill from Jira
    alone can set the cause family, feature and prevent for most DSS tickets. It cannot set the
    impact, and the fix type stays unclear for 11 of 45.
  - Two boundary questions for the team:
    - a `setting` family, for causes that are a Cerberus setting (DSS-1048, DSS-1204);
    - merging `outdated_install` into `environment` (DSS-945 and DSS-1018 sat on the line).
- **Outside the repo:**
  - PR #5 merged.
  - The DSS Triage Flow page was restructured: a contents column, live and planned tags, stale
    parts removed. It now holds the triage record proposal and Sean's planning use.
- **Not done:** the team's agreement on the record; nothing in the bot reads or writes it yet.

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
- **Planned (tracker Phase 3b, for both owners to agree):** standard inputs and outputs.
  - Input: code builds a ticket record before Claude starts. Each fact carries its source and
    is taken as of the ticket time: description as opened, project and workflow, versions then,
    inputs present, last failed run. Taking facts as of the ticket time is also the replay mode.
  - Output: Claude answers in one JSON schema, a shared part plus a details block per step or
    playbook. Code checks the required fields per status and renders the DM.
  - The design is on the DSS Triage Flow page.
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
