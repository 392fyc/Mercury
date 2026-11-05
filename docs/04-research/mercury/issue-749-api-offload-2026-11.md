# API credits for bounded work: findings and design (#749)

Status: draft. Phases 0 to 2 of the plan in #749 are research and design only; nothing here is built, and the pilot
(phase 3) needs its own go-ahead.

## Goal and constraints

The Claude and ChatGPT subscriptions each come with monthly API credit. The goal is to use that credit for bounded
work, so that interactive subscription sessions and the user's own attention go to what needs them. "Bounded" means
the input, the output and the acceptance check can all be written down before the work starts.

Hard constraints from the user:

- No change to the merge rule. Merge, delete, Issue close and push stay with an interactive session and the user.
- A model only returns a verdict. A script performs any action.
- No keys, private memory or raw logs in the repository or Issues.
- No general pull request reviewer at this stage. A reviewer that does not understand the architecture does harm (#476).
- lane-bus never answers on behalf of a session.

## What was measured

Evidence is in the #749 comments; the numbers below are the ones the design depends on.

### Update pull requests: no model needed where the tool states the reason

[laya](https://github.com/NandhaKishorM/laya) is a small local model that picks among listed answers in one forward
pass, without generating text. It was the first candidate for bounded decisions.

Over all 38 real `mercury-update` runs of two downstream projects, the best laya checkpoint reached 55.3% when given the
job status and the last 40 log lines. A plain match gets all 38 right, but that figure is circular: the labels were
derived from those same printed lines and, for the 8 dispatch-only runs, from the job list. What it does show is that
`tools/mercury_update.py` prints its own reason, so a model adds nothing in the cases seen.

The sample is small (the plan asked for 60) and holds no pending-check or conflict case, which are the cases that most
need explaining. So the conclusion is limited: where the tool already states the reason, a small script is enough. The
pilot moved to task routing.

### Routing: rules first, then a cheap model

The task: given an Issue as written before the work, predict whether it will change the harness and so must stay in an
interactive session. The samples are all 170 Issues linked to merged pull requests. The label comes from the files
those pull requests changed: `packs/`, `.mercury/`, `.claude/`, `.codex/`, `.agents/`, `harness/`, `AGENTS.md`,
`CLAUDE.md` or `mercury.json` make it `interactive` (134), anything else `api` (36).

| Method | Accuracy | Interactive tasks missed | API tasks kept interactive |
|---|---|---|---|
| Always interactive | 78.8% | 0 of 134 | 36 of 36 |
| Keyword rule | 81.8% | 21 of 134 | 10 of 36 |
| laya `typed-decisions`, not fine-tuned (best of three) | 61.2% | 42 of 134 | 24 of 36 |
| Claude Haiku 5.5, one prompt, low effort | 85.9% | 19 of 134 | 5 of 36 |
| Interactive if the keyword rule or Haiku says so | 87.6% | 9 of 134 | 12 of 36 |

- laya 0.4.22 did not meet the draft gate (at least 95% on confident answers, at most 40% abstention). Its confidence
  does not separate right from wrong: `multilingual` answers at 0.8 or above were 29.4% accurate. Under the user's
  decision, routing is therefore rules-first. Fine-tuning was not tried.
- Haiku cost $0.019 for the 170 calls, about 905 input tokens each.
- The last row sends 33 of 170 Issues to the API, and 24 of those are `api` under the label above. Under the wider
  post-work path list below, only 15 of the 33 pass and 18 would be stopped after the work, so more than half of what
  is sent would be wasted.
- All 15 are old: merged by 2026-05-23, mostly in `adapters/` and `mercury-gui/`, which no longer exist. Of the 62
  samples numbered 500 or above, none was both routed to the API and clean under that list. **With the repository as it
  is now, Mercury itself has almost nothing to offload.** The value, if any, lies in downstream projects and in jobs
  that change no repository files.

**Limits.** One repository; unbalanced classes; a broad label, since `.mercury/` once held non-harness files; a prompt
written once with no held-out set. The evaluation asked Haiku for one word and counted any reply without "interactive"
as `api`, the opposite of the designed default, and the designed JSON output will cost more tokens and may score
differently. Treat the figures as a direction, not a guarantee. Missing 9 of 134 means routing alone cannot protect the
harness, which is why the post-work check below exists.

### Waking a session: what the hosts do

Tested on the Claude desktop app, Claude Code 2.1.317, on Windows 11:

- An `asyncRewake` command hook that exits 2 woke an idle session, which replied within 3 seconds with no user input.
- A `Stop` hook re-armed the listener at the end of every turn, including woken turns.
- `timeout` is enforced and ends the process. A listener at `timeout: 7200` still woke the session at 7,055 seconds; one
  at `timeout: 700` was gone after about 700 seconds, and a later message waited for the next turn.
- A settings change reached the running session at its next hook run.
- Not tested: a `timeout` above 7,200 seconds, and a machine that sleeps while the listener waits.

Codex has no documented way to start a turn in an idle desktop session: "Finishing a background hook doesn't start a new
turn" ([Codex hooks](https://learn.chatgpt.com/docs/hooks)).

### When the money runs out

| Provider | Case | Response | What a job does |
|---|---|---|---|
| Claude | Workspace or organization spend limit you set | 400 `invalid_request_error`; a limit on the Claude Code workspace can return 429 instead | Stop and notify |
| Claude | Usage tier monthly cap | 429 `rate_limit_error`, no `retry-after`, keeps failing | Stop and notify |
| Claude | Billing problem | 402 `billing_error` | Stop and notify |
| OpenAI | Project or organization hard spend limit | 429, `error.code` `project_spend_limit_exceeded` or `organization_spend_limit_exceeded` | Stop and notify |
| OpenAI | Prepaid credit used up | 429, `error.code` `credit_balance_exhausted` | Stop and notify |
| OpenAI | Limit assigned by OpenAI | 429, `error.code` `organization_usage_limit_exceeded` | Stop and notify |

Sources: [Claude API errors](https://platform.claude.com/docs/en/api/errors),
[OpenAI error codes](https://developers.openai.com/api/docs/guides/error-codes),
[OpenAI spend limits](https://developers.openai.com/api/docs/guides/spend-limits). OpenAI identifies the cause by
`error.code`, not `error.type`, and says retrying does not restore access. Not verified: the exact error when a
subscription's included credit runs out, on either side; `credit_balance_exhausted` is the likely OpenAI candidate.

## Design

### Verdict schema

Every job asks for a JSON verdict and validates it before use. Anything that fails validation is treated as "no
verdict": the job notifies the user and acts on nothing. The model returns only a category and its evidence; the script
maps the category to an action, so a verdict cannot name an action at all. Task routing:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["task", "category", "evidence"],
  "properties": {
    "task": {"const": "task-routing"},
    "category": {"enum": ["interactive", "api"]},
    "evidence": {"type": "array", "minItems": 1, "items": {"type": "string"}}
  }
}
```

`evidence` quotes the input so a person can check the verdict. There is no `confidence` field: laya's confidence proved
misleading, and a model's self-reported number has not been shown to be better. The job adds the model id and the
request id to its own record, because a model should not report those.

### Context pack

A context pack is everything one request contains, and the script builds it, not the model.

- **Included:** the fixed instructions, the list of protected paths, the allowed categories, and the Issue title and
  body.
- **Excluded:** `.env`, memory, chat records, raw logs and anything outside the named input.
- **Issue text is data.** Anyone can write an Issue in a public repository, so a verdict can never grant anything; it
  only chooses a listed value. Remaining risk: a task sent to the API carries the same untrusted text into the work
  itself, and the post-work check looks only at paths, so a bad change outside those paths still depends on the normal
  pull request review.
- **Size:** no silent truncation. Input over the job's limit stays interactive without a model call. The
  evaluation above cut each body at 1,500 characters; a real job should measure what that cost.

### Routing rules

**Before the work**, applied in order; the first match wins:

1. The task's stated goal is itself a merge, delete, Issue close or push: interactive. The job definition states the
   goal, not the model. Delivering finished work does not count, because that push and merge always belong to an
   interactive session and the user.
2. The Issue text matches the harness keyword rule: interactive.
3. Otherwise ask the model. A verdict with `category` `api` offers the API route; anything else, including no verdict,
   means interactive.

**After the work**, on every task that took the API route: if the diff touches a protected path, stop, commit nothing
and hand the task to an interactive session. This is a plain check that needs no model, and it covers what routing
misses. Protected paths are the harness paths used as the label above, plus `tools/`, `sources/`, `scripts/`,
`.github/`, `.gitmodules`, `docs/01-rules/` and `docs/02-guides/git-flow.md`, because those hold the installer, external
sources, Git safety scripts, workflows and the written rules. A real job gives the model this same list.

The interactive default is deliberate: a wrong "interactive" costs subscription time, a wrong "api" risks the harness.

### Usage statistics and keys

- The monthly limit is the user's to manage; the jobs do not set or enforce one. Their part is to report usage.
- Each job writes one line per call (time, task, model, input and output tokens, cost, verdict) to an ignored local log,
  and never the input.
- A small report sums that log by month, task and model, so the user can see what the credit went on. Cost comes from
  the token counts in the API response and the published prices, so it is an estimate, not the provider's bill. At the
  measured rate one routing call costs about $0.0001.
- Keys stay in the ignored `.env` of the machine that runs the job. The Anthropic SDK reads `ANTHROPIC_API_KEY` by
  default, so a job using another variable name passes the key explicitly.
- Set `max_retries=0` and retry in the job: the Claude SDK retries 429 twice by default, which is wasted on a cap. A job
  first checks for the limit errors in the table above and stops on them. Only after that may it retry a 429 that
  carries `retry-after`, a 5xx or a connection error, a limited number of times.

### Where it runs

On one local machine first, as a Windows scheduled task. Move to a NAS once a job has run unattended for a month
without a false action. No job needs a GPU now that laya is out of the path.

### lane-bus delivery (separate lanes-pack Issue)

A seat is the slot a lane gives one session per host; messages are addressed to the seat. Two gaps remain. Delivery
happens at `SessionStart`, `UserPromptSubmit` and `Stop`, so no hook delivers in the middle of a turn. (The Claude CLI
also has a watcher, whose mid-turn behaviour was not checked.) An idle desktop seat depends on a background wait the
model starts itself.

- Deliver mid-turn through `PostToolUse`. Claude documents this. The Codex side is not wired in yet and its output
  format is unverified, so it must be tested first.
- Wake an idle Claude seat through `asyncRewake`, armed by `Stop`. The message must read as a peer message, because the
  host labels the wake as a hook error. A listener lock must be judged by its freshness: a killed listener leaves it
  behind.
- For an idle Codex seat, keep messages for its next turn and tell the user once.
- Unread messages must not pile up:
  1. Each delivery starts with a read and unread summary.
  2. By default a new send replaces the sender's last unread message, and the old one is recorded as merged. A sender
     can mark a message as separate, which keeps both.
  3. The cap (draft: 3) counts unread messages in one direction. At the cap a separate message is refused until the
     sender merges or the peer reads; a replacing send still goes through, because it adds nothing.
  4. The user is told only when unread goes from 0 to 1.

## Open questions

- Which first job goes to phase 3. Routing gives a verdict, but the API route needs a worker that does not exist yet.
- What such a worker is allowed to be. "A model only returns a verdict" rules out a model that edits files directly. One
  possible shape is that the model proposes a patch and a script validates and applies it. The user decides this before
  phase 3.
- How large a share of downstream project work is bounded. Only Mercury was measured, and recent Mercury work offers
  almost none. This decides whether an API worker is worth building at all.
- How often the user relays lane messages by hand. That count decides whether the undocumented `codex app-server` is
  worth researching.
- Whether #584 (a lightweight classifier as a harness pre-check) is answered by these results.
