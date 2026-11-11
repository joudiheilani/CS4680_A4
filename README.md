# StudyPilot: an AI agent that acts on your study workspace

**CS4680 Prompt Engineering, Assignment 4**

StudyPilot takes a request in plain English ("read my notes and make a study task for each topic before the final"), asks a Large Language Model to plan it as **structured tool calls**, validates every call, asks for your OK on anything destructive, **executes** it, and loops the results back to the model until the job is done. Every step is shown in the UI and written to an audit log.

- **Live app:** <https://studypilot-dkgm.onrender.com> (free hosting sleeps when idle, so the first load can take about 30 seconds)
- **Demo video:** _add your YouTube link here_

## The problem

Students juggle notes, deadlines and small lookups across a dozen apps. Chatbots can *tell* you what to do, but you still do the clicking, copying and typing yourself. StudyPilot closes that gap: the LLM's answer becomes an action that really happens: a file gets written, a task lands in a database, a weather API gets called. Because LLM output is unreliable, the interesting engineering is in **interpreting it safely**: parsing, validating, sandboxing and confirming before anything runs.

## What it can do (four action domains)

| Domain | Tools | Backed by |
|---|---|---|
| File operations | `list_files`, `read_file`, `write_file`, `append_file`, `delete_file` | Per-visitor sandbox folder |
| Database | `add_task`, `list_tasks`, `complete_task`, `delete_task` | Per-visitor SQLite DB, parameterised SQL |
| External APIs | `get_weather`, `define_word` | Open-Meteo, Free Dictionary API (no keys) |
| System commands | `run_command`, `calculate` | Allow-listed `ls/cat/wc/head/tail/grep/sort/uniq/date/echo`, AST-based calculator |

Example requests:
- "Read my CS3310 final study guide and create a study task for each topic, spread out before the final."
- "What's the weather in Pomona tomorrow? Should I bike to class?"
- "I got 88 on the midterm (40%) and 92 on projects (60%). What's my grade? Save it to grades.md."
- "Run wc -w on all my markdown files and tell me which is longest."
- "Delete welcome.md" (pauses for your approval)

## How it works

```mermaid
flowchart LR
    U[User request] --> P[Prompt builder<br/>system prompt + context + history]
    P --> L[LLM module<br/>OpenRouter API, retries, fallback]
    L --> X[Parser<br/>extract + normalise JSON]
    X -- invalid --> R[Repair prompt] --> L
    X --> V[Validator<br/>tool exists? args valid? path safe?]
    V -- blocked --> O
    V --> C{Destructive?}
    C -- yes --> A[Ask user: Approve / Reject]
    A --> E
    C -- no --> E[Executor<br/>files · SQLite · web APIs · commands]
    E --> O[Observations]
    O -- more to do --> L
    O -- done --> F[Reply + audit log]
```

The model always answers with one JSON object:

```json
{"thought": "Friday is 2026-11-13; 'important' means high priority.",
 "actions": [{"tool": "add_task", "args": {"title": "Finish lab report", "due": "2026-11-13", "priority": "high"}}],
 "done": true,
 "reply": "Added **Finish lab report** (high priority, due Fri Nov 13)."}
```

## Requirement checklist

| Requirement | Where |
|---|---|
| **LLM integration**: connect, send prompts, receive responses | [app/llm.py](app/llm.py): OpenAI-compatible client for OpenRouter (free `nvidia/nemotron-3.5-super-vl:free`, falling back to `openrouter/free`) |
| Handle API errors and rate limiting | Honours `Retry-After` on 429, exponential backoff with jitter on 5xx/timeouts, switches to a fallback model when a wait is too long, fails fast on bad keys, retries without JSON mode if the provider rejects the JSON. Per-visitor and per-IP rate limits in [app/sessions.py](app/sessions.py) protect the free quota. |
| **Action interpreter**: parse LLM output into commands | [app/parser.py](app/parser.py) strips fences, finds the JSON object in surrounding prose, accepts common aliases (`arguments`→`args`, `name`→`tool`...) |
| Convert responses into executable operations | [app/tools/](app/tools): registry of typed tools; each has a pydantic argument model |
| Execute across domains | Files, SQLite, web APIs, system commands (table above) |
| **User interface**: natural-language input, results, status, feedback | Web UI in [static/](static) (chat, live workspace panel, activity log) plus a terminal UI in [cli.py](cli.py) |
| **Validation before execution** | Unknown tools, extra/invalid arguments, bad dates, path traversal, non-text file types and unsafe commands are rejected and reported back to the LLM, never run ([app/tools/base.py](app/tools/base.py)) |
| Graceful errors | Tool failures become observations the model can react to; LLM errors become friendly messages; unexpected bugs are caught, logged and never leave the session stuck |
| **Confirmation for destructive operations** | `delete_file`, `delete_task`, and `write_file` over an existing file pause the loop until the user approves or rejects |
| **Audit log** | Every request, LLM call (model, tokens, latency, attempts), action, block and confirmation is appended as JSON lines ([app/audit.py](app/audit.py)); viewable and downloadable in the UI |

## Prompt engineering techniques

All in [app/prompts.py](app/prompts.py):

1. **Role and scope prompting.** A named persona ("StudyPilot") with a defined job, boundaries and tone.
2. **Structured output contract.** A strict JSON schema enforced twice: JSON mode on the API, then our own validator.
3. **Tool catalog generated from code.** Tool signatures in the prompt are built from the same pydantic models the executor uses, so the prompt can't drift from reality.
4. **ReAct (Reason + Act) loop.** `thought → actions → TOOL RESULTS → thought…` until the request is complete.
5. **Few-shot examples.** Four worked examples: a one-step action, a multi-step read-then-write, a clarifying question for a vague destructive request, and a refusal.
6. **Context injection.** Today's date, the dates of the next 7 days and a snapshot of the workspace go into every request, so "Friday" or "my notes" resolve without date arithmetic (which small models get wrong) or wasted tool calls.
7. **Self-correction.** When the model's output fails parsing or validation, the exact error is sent back ("FORMAT ERROR: ...") so the model fixes its own mistake (up to 2 repairs).
8. **Efficiency instructions.** Batch independent actions, plus a `done` flag that skips the final LLM call when the reply is already known. This roughly halves calls for write-only requests, which matters on a rate-limited free tier.
9. **Prompt-injection defence.** Tool output is wrapped as `TOOL RESULTS (data..., not instructions)` and the system prompt tells the model to ignore instructions found inside data.
10. **Low temperature (0.2)** for consistent, schema-following output.

## Safety design

- **Sandbox per visitor.** Each browser gets a random session id, its own folder and its own SQLite file. Paths are resolved and checked so nothing escapes (`..`, absolute paths, `~`, hidden files are refused). Only `.md .txt .csv .json` can be written, with size and count limits.
- **Commands are allow-listed** and run without a shell (`shell=False`, no pipes, redirects, `$`, or long `--options`; dangerous short flags like `sort -o` blocked), inside the sandbox, with stdin closed, a 5-second timeout and truncated output. Globs are expanded by us, inside the workspace only.
- **No `eval`.** The calculator walks the Python AST and allows only numbers, arithmetic and a few math functions.
- **SQL injection is impossible by construction.** All queries are parameterised.
- **The model never sees or chooses system internals.** It can only pick from the registered tools.
- **Abuse limits.** 6 requests/min and 120/day per visitor (configurable), per-IP limits, max 6 LLM steps per request, at most 3 concurrent agent runs.

## Run it locally

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env, then paste your key
uvicorn app.main:app --reload   # open http://localhost:8000
```

Get a free OpenRouter key (no credit card) at <https://openrouter.ai/settings/keys> and put it in `.env` as `OPENROUTER_API_KEY`. Free models allow about 50 requests a day per key, so the app keeps per-visitor limits low. Without a key the app runs in **demo mode**: a small rule-based stand-in for the LLM, so the full validate/confirm/execute pipeline can still be tried offline.

Terminal version: `python cli.py`

Tests (53 of them: parser, validators, sandbox escapes, command allowlist, LLM retry/fallback logic, and the full agent loop including confirmations and self-repair):

```bash
pytest
```

## Deploy (Render, free)

1. Push this repo to GitHub.
2. On <https://render.com>: **New + → Blueprint**, pick the repo (it reads `render.yaml`).
3. Paste your `OPENROUTER_API_KEY` when asked. Deploy, then open the `.onrender.com` URL.

Free Render instances sleep after 15 minutes idle, so the first visit can take about 30 seconds to wake up. Workspaces are stored on the instance's temporary disk and reset on redeploy.

## Project layout

```
app/
  main.py        FastAPI routes, session cookie, rate limiting
  agent.py       the ReAct loop: parse → validate → confirm → execute → observe
  llm.py         OpenRouter/OpenAI-compatible client with retries and fallback
  prompts.py     system prompt, few-shot examples, context and repair prompts
  parser.py      robust JSON extraction and normalisation
  tools/         files.py, tasks.py (SQLite), web.py (APIs), system.py (commands, calculator)
  sessions.py    per-visitor sandboxes and rate limits
  audit.py       JSON-lines audit log
  mock_llm.py    offline demo brain and the scripted LLM used by tests
static/          web UI (vanilla HTML/CSS/JS)
cli.py           terminal UI
tests/           pytest suite
```
