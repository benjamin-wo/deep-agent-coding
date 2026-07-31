# deepagents + DeepSeek + Gemini + Telegram + E2B + Railway status, on Railway

A 24/7 coding agent you talk to over Telegram. **DeepSeek** (`deepseek-v4-pro`)
powers the actual coding/agentic work. **Gemini** handles vision and audio --
when you send a photo or voice note, Gemini turns it into text first, then
that text goes to the DeepSeek agent as a normal instruction. Gemini never
touches code, sandboxes, or repos; it's purely a front-end transcriber.

It runs code and manages git in an on-demand sandbox (created fresh per
conversation, auto-expiring when idle so cost stays near zero), can push to
your repos only with your approval, and can check the deployment status of
your *other* Railway projects read-only.

This is meant to be one independently-deployed project among several: your
other agent projects (e.g. `ben-hermes-agent`, `agents-anywhere`) each live
in their own repo and their own Railway service. This coding agent doesn't
run inside them -- it just has read access to check on them.

## What this is
- `agent.py` -- Deep Agent using `ChatDeepSeek` as the model. Each Telegram
  conversation gets its own on-demand E2B sandbox (auto-expires after
  `SANDBOX_IDLE_TTL_SECONDS` of inactivity -- default 20 min -- so you're
  not paying for idle compute). A custom `push_to_github` tool gates every
  push behind a LangGraph `interrupt()`. `list_railway_projects` /
  `check_deployment_status` are read-only tools hitting Railway's GraphQL
  API to check on your other projects. A SQLite checkpointer persists
  conversation history and any pending approval across restarts.
- `multimodal.py` -- Gemini-backed `describe_image` / `transcribe_audio`,
  called from `main.py` before a photo or voice note ever reaches the agent.
- `main.py` -- FastAPI app, one route: `/telegram/webhook`. Detects message
  type (text, photo, voice, audio) and converts to text accordingly.
- `Dockerfile` -- builds and runs the service on Railway.

## How push approval works
1. You ask the bot to make a change and ship it.
2. It clones/edits/tests inside its sandbox, then calls `push_to_github`.
3. That pauses the conversation and Telegram sends you:
   ```
   Approve this push?
   Repo: /home/user/my-repo
   Branch: main
   Message: fix off-by-one in pagination

   Reply 'yes' to push, anything else to cancel.
   ```
4. `yes` -> it pushes. Anything else -> cancelled, nothing reaches GitHub.

## How Railway status checks work
The agent can answer things like "how's ben-hermes-agent doing?" by calling
`check_deployment_status`, which sends a read-only GraphQL query for that
project's last 3 deployments (status + timestamp). It cannot start, stop, or
change anything -- the tool only ever sends that one query, regardless of
what the model is asked to do. If you later want it to actually restart a
crashed service, treat that the same as a push: add a new tool gated behind
`interrupt()`, don't just hand it a raw account token and CLI access.

## Setup

### 1. Telegram bot token
Message **@BotFather** -> `/newbot` -> copy the token.

### 2. DeepSeek API key
https://platform.deepseek.com -> API keys. This powers the actual coding
agent (`DEEPSEEK_API_KEY`, model defaults to `deepseek-v4-pro`).

### 3. Gemini API key
https://aistudio.google.com/apikey -> used only for turning photos/voice
notes into text before they reach the DeepSeek agent (`GEMINI_API_KEY`).

### 4. E2B API key
https://e2b.dev/dashboard

### 5. Scoped GitHub token
GitHub -> Settings -> Developer settings -> **Fine-grained personal access
token**, scoped to only the repos you want touched, with Contents: Read and
write.

### 6. Railway account token + project IDs (for status checks)
1. https://railway.com/account/tokens -> create a token. This is
   account-scoped (sees everything in your account), which is what makes
   cross-project status checks possible -- but also means it's not
   inherently read-only. This service enforces read-only by only ever
   sending the `deployments` query; don't add other Railway operations
   without an approval gate.
2. For each other project you want status on: open it in the Railway
   dashboard, press Cmd/Ctrl+K, and copy its Project ID, Service ID, and
   Environment ID.
3. Build the `RAILWAY_PROJECTS` JSON, e.g.:
   ```json
   {"ben-hermes-agent": {"project_id": "...", "environment_id": "...", "service_id": "..."}}
   ```

### 7. Push this repo and deploy on Railway
```bash
git init && git add . && git commit -m "deepagents telegram coding service"
gh repo create my-deep-agent --private --source=. --push
```
Railway: New Project -> Deploy from GitHub repo -> this repo (Dockerfile
auto-detected). Set env vars from `.env.example`. Attach a **Volume** at
`/data` so conversation + approval state survives redeploys. Generate a
public domain.

### 8. Point Telegram at your Railway URL
```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://<your-railway-domain>/telegram/webhook" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

## Notes / known gaps
- **Photos/voice cost an extra Gemini call before the agent even starts.**
  Fine for occasional use; if you send a lot of voice notes, factor in
  Gemini's per-call cost on top of DeepSeek's.
- **Gemini calls in `multimodal.py` are synchronous**, so they briefly block
  the event loop during image/audio processing. Not an issue at personal-bot
  volume; wrap in `run_in_executor` if you ever need real concurrency.
- **Sandboxes are per-conversation, not shared.** Each Telegram chat gets
  its own E2B sandbox, torn down and recreated after `SANDBOX_IDLE_TTL_SECONDS`
  of inactivity. Cost scales with actual active use, not wall-clock time.
- **Repos get re-cloned each time a sandbox expires.** That's the
  cost/persistence trade-off of the ephemeral design -- fine for occasional
  coding sessions, adds ~10-20s at the start of a session.
- **RAILWAY_API_TOKEN is account-wide.** It can technically do more than
  read. This codebase only ever uses it for one read query. If you extend
  it, extend the safety model (approval gate) alongside it.
- **Multi-instance caution.** If Railway restarts this service mid-approval,
  the pending interrupt is safe (persisted in SQLite), but any in-progress
  uncommitted sandbox work is not. Commit early within a session.
