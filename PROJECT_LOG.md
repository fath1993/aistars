# AIstars — Project Log

A Telegram bot where people chat with AI, create and edit images, turn voice
into text and text into speech, and pay with Telegram Stars. OpenAI is the
default provider; Anthropic can be added. Built from MyShortClip's code: the
same registration, languages, credits, safety checks, admin and deployment.

Stack: Django 6.1, Celery + Redis, Postgres, Docker Compose, the project's
own nginx container behind the server's edge proxy (`server-edge`).
Settings split as `aistars/settings.py` with
`aistars/c_settings/{dev,prod,test}.py`; project apps under `apps/`
(`core`, `ai`, `billing`, `safety`, and `studio` for the AI tools).

## Rules (read before every change)

1. Read this whole file before starting any request.
2. Every change gets a new entry at the top of **Changelog**.
3. Bump `APP_VERSION` in `.env` and use the same version in the entry.
   - Patch (0.1.**x**): fixes and small changes.
   - Minor (0.**x**.0): new features.
   - Major (**x**.0.0): breaking changes.
4. **Date = the real date of the day the change is made.** Check today's date before writing. Never copy the date of the previous entry: work done tomorrow gets tomorrow's date.
5. **Title**: short, imperative, under 72 characters. It is the last part of the commit subject (rule 8).
6. **Description** = what changed and why.
7. **Every request is recorded.** A request that changes files gets a Changelog entry. A request that changes nothing (a question, a review, advice) gets one line at the top of **Requests**: date — what was asked — the answer in short.
8. **Changes go straight to git.** Every change is committed and pushed to `main` of `fath1993/aistars`. Commit subject = `date — vX.Y.Z — title`, matching the entry's header; body = the entry's description. A request that changes no files is committed as `date — Log request: <what was asked>`. No zip files.
9. **Deployment standard.** Before every change, check the Deployment standard below; if the change touches any item, update that item in the same change.

## Deployment standard

The same in every project on the server, so a new project starts by copying
it (item 14). Rule 9: before every change, check each item; if the change
touches one, update it in the same change.

1. **Repository.** A private GitHub repo. `main` is what runs in production. `.env` is committed with the production values and never edited on the server: each deploy resets the server to the repo.
2. **Configuration.** `.env` holds only what must exist before the database: secrets, database credentials, hosts, `APP_VERSION`. Everything else lives in the admin's Runtime settings. A new setting is a registry line, plus an `.env.example` line if it belongs in `.env`. A project without a database keeps all its settings in `.env`.
3. **Containers.** Docker Compose with a pinned project name and its own network. The database and Redis publish no ports. Admin-only tools (pgAdmin) bind to 127.0.0.1.
4. **HTTPS.** Cloudflare does HTTPS for every domain: a proxied DNS record, SSL mode Flexible. Cloudflare to the server is plain HTTP, permanently. There are no certificates on the server and nothing listens on port 443.
5. **Edge.** The server runs one edge proxy, the `server-edge` repo (Traefik), on port 80. It routes each domain to the project that claims it and passes Cloudflare's `X-Forwarded-For` and `X-Forwarded-Proto` through. No project edits the host nginx.
6. **Project nginx.** Each web project has its own nginx container, configured in `deploy/nginx/` in its repo. It serves static files read-only from `/var/www/<project>/static` and public media read-only from the project's media folder, and proxies the rest to the app, passing `X-Forwarded-Proto $http_x_forwarded_proto`, never `$scheme`. It looks the app up by name at every request (`resolver 127.0.0.11` and a variable in `proxy_pass`), never once at start. Files the app serves itself (WhiteNoise static, media only admins may see, e.g. invoices in `/var/www/<project>/media`) stay with the app, which checks who asks.
7. **Routing.** The project nginx carries the edge labels: `traefik.enable=true`, `traefik.docker.network=<project network>`, `Host(<domain>)`, entry point `web`, port 80. The domain equals `ALLOWED_HOSTS` in `.env`, checked by a test. A new domain needs only a proxied Cloudflare DNS record.
8. **Deploy script (`dp.sh`).** Runs as root. Deploys a given commit, refused unless it is on `main`, or the latest `main`. Rebuilds the containers (the app migrates on start) and recreates the project nginx (a running nginx keeps the config and folders it started with), fixes ownership and permissions, waits for migrations, runs `doctor --strict`, and exits non-zero if it fails. A project without a database waits for the app to answer instead of for migrations.
9. **CI/CD.** `.github/workflows/deploy.yml`: on every push to `main`, the tests and (Django) the missing-migration check, then a deploy of that exact commit over SSH. One deploy at a time; **Run workflow** redeploys.
10. **Deploy key.** One SSH key per project, made on the server with `setup-deploy-key.sh <project>` from `server-edge`. GitHub secrets `DEPLOY_HOST`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`. Locked in root's `authorized_keys` to the project's `deploy/ci-deploy.sh`, which accepts only a commit ID. The host key is pinned, and the private key is deleted from the server after setup.
11. **Health.** `doctor` (`manage.py doctor`, or `doctor.py` without Django) checks the configuration and every external service the app needs, and prints the fix for each failure. Where the admin has a System status page, it shows the same checks. Every new external service or required setting gets a check.
12. **Tests.** Run in CI without external services (Django: the test settings, SQLite in memory; without Django: pytest), including the log and version consistency test, the domain test (item 7) and a minimum test count that only goes up.
13. **Secrets.** `.env` stays out of Docker images (`**/.env` in `.dockerignore`) and is `0640` on the server. No secrets in logs.
14. **New project.** Copy `Dockerfile`, `docker-compose.yml`, `dp.sh`, `deploy/ci-deploy.sh`, `deploy/nginx/`, `.github/workflows/deploy.yml` and `deploy/README.md` from the closest project (MyShortClip for Django, SpotFarsi FastAPI without Django). Rename the project, network, paths and domain. Then add the proxied Cloudflare DNS record, clone the repo into `/var/www/<project>` on the server, and set up its deploy key (item 10).
15. **Firewall.** The server sits behind a Hetzner Cloud Firewall (`FIREWALL.md` in `server-edge`): SSH (22) open, HTTP (80) only from Cloudflare's IP ranges, everything else closed, so the server's IP answers nothing on its own. A project never needs a firewall change.

## Changelog

### 2026-10-22 — v0.7.0 — My finances: money in, costs, providers, invoices

The owner could not see easily what the bot costs: as the one who pays for
everything, he wants one section of his own to manage billing, payments and
invoices. The plan sent (an overview, provider balances, Telegram Stars,
bills with invoices, AI calls recorded one by one) was confirmed ("perfect");
the two choices left to me: USD with EUR at a rate he sets, and invoice files
on the server, private.

**Every AI call is recorded** (`ai.AICall`, written where every call is
priced): provider, model, purpose (chat answers, images, voice to text, text
to speech; safety judge, translations, chat memory, chat titles, Find for me,
drafted agents; other), cost and tokens. Until now only hourly totals were
kept (SpendLedger), so earlier months show those as "before calls were
recorded one by one". AI → AI calls lists them (superusers).

**💼 My finances** (new app `finance`, superusers only):
- **Overview** for any month or all time: money in (Stars paid less refunds,
  at the Star value, payments and buyers), money out (AI by provider and by
  purpose, split into users' requests and the bot's own work; bills), profit
  and margin, also in EUR; credits users still hold, worth so much and able
  to cost at most so much at the providers; day by day; a CSV for the
  accountant (payments, refunds, AI cost per day and provider, bills).
- **What is left at the providers:** payments to OpenAI or Anthropic are
  bills with that payee; paid minus what the bot used since the first one is
  the estimated balance, red below **Warn me below (USD) at an AI provider**
  (10), and the owner gets a Telegram message once a day (hourly check).
  With an optional OpenAI **Admin key** (AI → Providers), OpenAI's own Costs
  API figure is shown next to the bot's.
- **Bills & invoices:** date, payee (OpenAI, Anthropic, server, domain,
  other), amount in USD or EUR (**One EUR in USD**, 1.15), invoice number and
  file, paid, **Repeats monthly** (an unpaid copy each month, daily task).
  Payments to AI providers count toward the balance there, not as costs: the
  AI is counted call by call.
- **Telegram Stars:** the bot's balance and its last 100 transactions
  (payments, refunds, withdrawals) from Telegram (getMyStarBalance,
  getStarTransactions).
- **Invoice files** live in `/var/www/aistars/media` (a new bind mount,
  `MEDIA_ROOT`), opened through the admin by superusers only; nginx has no
  /media/. Deployment standard item 6 says so.

Tests on SQLite and, for the new queries, on Postgres. 620 tests.

### 2026-10-22 — v0.6.0 — Find and pick the archive channel in the admin

The owner made the bot an admin of a private channel and posted in it, and
nothing happened: the archive channel was never recorded, and there was no
way to get the channel's ID from the admin. Telegram did not send the bot
the posts (`channel_post` was not among the updates asked for), and the
"made admin" notice is used only when it comes from Your Telegram ID.
- Telegram is asked for `channel_post` too. Nothing is done with a post; it
  is kept like every update (Telegram updates).
- **Control panel → Archive channel** (`/admin/core/controlpanel/archive/`):
  the current archive with its title and **Stop archiving**; the steps
  (make the bot an admin with Post messages, post anything, reload); every
  channel the bot heard from in 30 days, from posts and from being made admin
  by anyone, with its ID, when, and the bot's status, each with **Use as
  archive**; and a box for a channel ID or public @username. Using one checks
  the channel with Telegram and has the bot post "✅ This channel is now the
  AIstars archive." there; only then is it saved, so a channel the bot may not
  post in is refused with the reason.
- While Telegram is not sending channel posts (the webhook was registered
  before this version), the page says so with a **Register the Telegram
  webhook** button, and the doctor warns.
- Runtime settings, the doctor's fix and deploy/README point to the page.
- Tests for the page, picking, refusals, typing an ID or @username, stopping,
  the webhook warning.

### 2026-10-22 — v0.5.0 — Keep every Telegram update, and handle each once

The owner wants the warehouse bot's Telegram updates section here too: there
every update is stored as received and shown read-only in the admin. AIstars
handled updates and kept nothing, so whether Telegram ever sent the "made
admin of a channel" notice for the archive could not be seen. Asked how long
to keep them, the owner left it to me: 90 days, adjustable (the chats,
payments and requests they made stay regardless).
- `TelegramUpdate` (AIstars → Telegram updates, `apps/core/updates.py`): the
  update as it came, its type, the user or chat it came from, a preview (the
  text, the button, "[file: cv.pdf] caption", "shared a phone number", "paid
  250 XTR", "channel “Archive”: left → administrator"), the outcome
  (Handled, Failed with the error, Rejected), how many times Telegram sent
  it, and when. Read-only; the sender links to their account, and each
  account links to everything it sent. One query for the list's accounts.
- **Handled once.** Telegram sends an update again when the answer is slow;
  the webhook handled it again (a second answer, a second charge). The
  update_id is unique now: a repeat only counts a delivery.
- **Failures** keep their error with the bot token removed (a Bot API URL
  carries it); the activity log line is cleaned the same way.
- **Rejected** deliveries (wrong secret) are listed too, at most one a
  minute and at most 20 KB of body, without an update_id so a forged one
  never blocks the real update.
- **Telegram updates kept for (days)**, 90 (0: forever), deleted every night
  at 04:40 UTC (`purge_telegram_updates`).
- Tests for all of it, and for a chat message sent twice being answered and
  charged once.

### 2026-10-21 — v0.4.2 — Keep the project nginx pointed at the app

After the server's edge took over port 80, aistars.myshortclip.com answered
5xx for every page and 404 for static files, while the other sites worked.
The project nginx had been started by the first manual deploy; the GitHub
deploy then recreated the app (and Celery) containers, which can come back
on other addresses, and nginx had looked `aistars` up once, at start, so it
kept sending requests to the old address. It also keeps the config file and
static folder it was started with: git replaces files rather than editing
them, so a changed `deploy/nginx/aistars.conf` never reached it, and compose
does not recreate nginx when only the app changed.
- `deploy/nginx/aistars.conf`: the app is looked up at every request through
  Docker's DNS (`resolver 127.0.0.11 valid=10s`, `proxy_pass $app`).
- `dp.sh`: after the build, nginx is recreated every deploy
  (`up -d --no-deps --force-recreate nginx`), about a second without it.
- Deployment standard items 6 and 8 say both, so every project does the
  same (MyShortClip has the same two gaps).
- Tests for both.

### 2026-10-21 — v0.4.1 — Deleting a chat only hides it from the user

The owner: nothing is ever deleted from the archive channel, and a user's
delete does not delete from the database: the user no longer has the chat
and it is not shown to them; on our side it is only a checkbox, "deleted".
v0.4.0 had deleting a chat clear its words from requests and safety checks
and delete its archive copies, and chats unused for 180 days deleted every
night. All of that is gone:
- Conversation **deleted** (a checkbox in AI tools → Conversations, with a
  filter). 🗑 Delete and Delete all set it; messages, memory, requests,
  safety checks and archive copies all stay.
- Everything the user sees leaves deleted chats out (`Conversation.of`): the
  current chat, My chats, search, Recent agents, opening and closing files,
  a confirmed message, a voice note heard after its chat was deleted (it goes
  to a chat they have), and Try again ("You deleted that chat").
- No nightly purge, no **Delete chats unused for (days)** setting, no
  archive deletion (and no record of archive copies per request: migration
  0006, not deployed yet, regenerated).
- The confirmations say the chat "goes from your chats"; `/privacy` says a
  deleted chat is gone from the user's chats and the owner keeps a record,
  and lists the requests, safety checks and archive copies as kept.

### 2026-10-20 — v0.4.0 — Chat memory, files that close, caching, privacy

The owner asked how chat history is handled. As built, the last 20 messages
went with each new one and older ones were dropped silently; photos and
files went again with every follow-up; and deleting a chat left the user's
words in the requests and safety checks and their files in the archive.
Proposed (Requests, below); the owner chose the AI-written chat memory, left
the retention to me (180 days), keeps full admin access ("admin is admin"),
and left About me to me (in now).

**Chat memory.** A chat's newest messages go word for word up to **Chat
memory, word for word (tokens)** (8,000, replacing Chat memory (messages));
older ones are folded by AI into the chat's memory, which goes with every
message after the instructions, the agent's role and About me. Folding runs
in the background after an answer, only once the messages after the memory
pass the budget, and folds the oldest (a question with its answer, never the
last two) until half is left: now and then, not every message, so the start
of the request stays the same for the providers' cache. It uses the model
marked Default for translation, keeps facts, decisions, numbers and what
files contained, treats the chat as data, is free for users and counts
against the AI spending limits; Demo mode writes a demo memory; without a
usable model the oldest messages wait and are left out (the doctor warns).
The chat screen says how many messages are in the memory; the admin shows
it. The same step names each new chat with a few words in its language.

**Files.** A photo or file goes again with **Files go with (messages)** (5)
messages of its chat, then the memory keeps what was said about it. The chat
screen lists the files that go again, with what each adds to a message (up
to 🪙 15 for a 30-page PDF on GPT-6.1 Sol, under 🪙 1 for a photo), with
✖️ Close and Close all; an answer has 📎 Close the files while some go
again. A file sent again is open again. A question's own files always go.

**Prompt caching, priced right.** Checked today: OpenAI caches the start of
every request on its newer models by itself and bills cache writes at 1.25×
and reads at 0.1× the input price (0.05× on GPT-6.1 Sol); Anthropic caches
when asked (top-level `cache_control`), writes 1.25× (5 minutes), reads 0.1×
(0.05× on Sonnet 5.5). Every chat was being priced as plain input:
writes under-charged, reads over-charged. Now each model has cache write
and read factors (AI → Models; migration ai 0003 seeds them); the clients
read `cached_tokens`/`cache_write_tokens` (OpenAI) and
`cache_read_input_tokens`/`cache_creation_input_tokens` (Anthropic, added to
its input) and price each part; chats send `prompt_cache_key` (their own,
also keeping users' caches apart) and ask Anthropic to cache. Holds count all
input at the write price. 🧾 Details says how much came from the cache.

**Your chats.** 🧹 Start fresh: a new chat with the same agent whose memory
carries what matters of the old one ("Send it in a new chat" does the same).
📤 Export: the chat as a text file. My chats: 🔍 Search (titles and
messages) and 🗑 Delete all. A chat reopened from My chats is the chat screen
with its last message and 🗑 Delete. 📝 About me (Profile): up to 600
characters every chat gets, checked like a chat message, cleared any time.

**Privacy.** Deleting a chat (one or all) removes its messages and memory,
its words and files from its requests (amounts stay on statements) and from
the safety checks it passed (a blocked one stays: the record of a strike),
and its copies in the archive channel (now recorded per request; Telegram
needs the bot's Delete messages right there after 48 hours, and the activity
log says when some stay). Every night (03:40 UTC) chats unused for **Delete
chats unused for (days)** (180; 0 keeps them) go the same way, with the
words of older requests outside chats and old passed checks. `/privacy` and
Profile → 🔒 Privacy say what is kept and for how long, that the providers
get each request (OpenAI asked not to store it), and that the owner can read
chats. Help explains the memory, files, About me and /privacy.

Tests: caching in both clients, folding, naming, the window, files closing
and reopening, Start fresh, About me, export, search, deleting everywhere,
the nightly purge, /privacy, the doctor. 584 tests.

### 2026-10-20 — v0.3.0 — Agent catalog, costs on every request, Usage page

The owner: when starting a chat, the user should choose the kind of
assistant (predefined agents with their own instructions: a lawyer, a
teacher, a translator, a programmer…), from a catalog they can browse by
category or search; every request's cost and usage must be shown; the menus
must let users move freely everywhere; "make my idea richer with your
insight". The plan sent (Requests, below) was confirmed with "go ahead".

**Agents.** Assistants are agents now, with **Categories** (emoji, name,
order; names translated like the bot's texts), starter questions, search
words and **Featured**. 39 agents in 10 categories to start (Law, Learning,
Code & tech, Languages, Work & business, Writing, Money, Health & fitness,
Creative, Daily life; migration 0005, updating the eight there were). Law,
health and money agents give general information and say when a
professional is needed; the harder jobs (lawyer, contracts, programmer,
business…) use GPT-6.1 Sol, the rest the user's model.
- 🧩 Agents: the categories, 🔍 Search, ✨ Find for me, ⭐ My agents,
  🕘 Recent and 🔥 Popular (most used this month). Screens show where they
  are: "🧩 Agents › ⚖️ Law".
- An agent's card: what it does, its three questions, its model with the
  price of an everyday answer, ▶️ Start a chat, a question to start with,
  ⭐ Save, 📤 Share (Telegram's share sheet with
  `t.me/<bot>?start=agent_<slug>`, which opens the card; a new user gets it
  once registered). ‹ Back returns to the list it came from (a category
  page, search results, found agents, the chat).
- Search: words in the name, line and search words, in English and the
  user's language; a word most agents have counts for little, common words
  none ("write my cv" → CV & cover letter).
- ✨ Find for me: the user describes the need, the model marked Default for
  translation picks up to five agents (the user's words as data, not
  instructions). Free, 20 a day (Runtime settings → **Find for me, per user
  a day**), counted only when the AI answered; in Demo mode, or when it
  fails, a word search.
- ➕ New chat asks who to talk to: "Again with" the last agent, the General
  assistant, the featured agents, All agents, Search, Find for me, My
  agents. The chat screen names the agent, its line, the model and the
  everyday price, and offers the agent's questions while the chat is empty
  (a tapped question is shown as asked, then sent like a typed message).
- For the owner: the Agents list shows each agent's chats and credits
  charged (requests now keep their agent, even when the chat is deleted);
  Category → **Draft 3 new agents with AI** writes agents switched off, to
  read and switch on; the doctor counts agents and warns about agents in no
  category.

**What things cost.**
- Every answer, image, voice note read aloud and text heard ends with what
  it cost and the balance, and has **🧾 Details**: a box with the model, the
  agent, the tokens read and written (as the provider reported them, now
  kept on each request), the length or characters for voice, and the hold:
  charged and returned.
- A chat message that can cost more than **Ask before sending above**
  (Runtime settings, 🪙 20; 0 never asks) waits: "This message can cost up
  to 🪙 44", with Send, Cheaper model and Cancel, and, when most of the price
  is the chat so far, **Send it in a new chat**. Nothing is checked or held
  before the yes. Once agreed in a chat, only a message that can cost half
  as much again asks, so an expensive model does not ask every time.
- Below **Low balance warning** (Runtime settings, 🪙 20) results say "Your
  balance is low" with Buy credits.
- **📊 Usage** (home, `/usage`): today, 7 and 30 days (credits and
  requests), the last 30 days by tool, agent and model, the last requests
  page by page, and a **daily limit** users may set themselves (none, 🪙 20
  to 🪙 1,000): a request that would pass it, counting what is held, is not
  sent and says so, with the way to change it.

**Getting around.** Home: ➕ New chat · ▶️ Continue (once there is a chat)
/ 🧩 Agents · 🎨 Images · 🎙 Voice / 🪙 Credits · 📊 Usage / 👤 Profile ·
❓ Help. Every screen keeps ‹ Back · 🏠 Menu. Commands `/new`, `/agents`,
`/chats`, `/usage` with `/images`, `/voice`, `/menu`, `/help`; Help lists
them and explains agents and costs.

Tests: catalog, cards, starting and asking, saving, recent and popular,
search, Find for me (AI, failure, none left, Demo), links before and after
registering, drafting, admin counters; confirmation, daily limit, Details for
every kind of request, low balance; Usage. 546 tests.

### 2026-10-20 — v0.2.0 — Read every kind of file, and an album as one message

The owner: the bot must take "images, files, voices, PDFs… all kind of
files which the API supports", and asked what happens with several files
or photos at once. The plan sent (Requests, below) had files without a
question wait as attachments with a Send button; the owner refused that:
"just one message but contain many". So a message is one question, however
many photos or files it carries, with no extra step.

**Every file OpenAI reads.** Chat Completions reads only PDFs; OpenAI's
Responses API reads PDFs (their text and page images), Word and other
documents, PowerPoint, Excel/CSV (the first 1,000 rows a sheet), text and
code (developers.openai.com, File inputs, read today). So OpenAI's chat
moved to the Responses API (`/v1/responses`): `instructions` for the system
text, `input` with `input_text`, `input_image` and `input_file` (the file
as a data URL), `max_output_tokens`, and `store: false` (nothing kept at
OpenAI). Its stream is read from `response.output_text.delta`, with the
usage from `response.completed` or `response.incomplete` (cut at the
longest answer); `response.failed` and `error` fail the request. The
translator and the safety judge use it too. It also makes web search
possible later.
- Claude's models get PDFs as documents and text files as text documents;
  other files are refused before anything is held ("Claude Haiku 5.5 cannot
  read cv.docx. Choose another model").
- A PDF needs a model that sees images (its pages are read as images too).
- Images sent as files (JPEG, PNG, WebP, GIF) are photos.
- Videos: "cannot be read yet"; anything else names the file and what the
  AI reads.

**Limits.** 20 MB a file (the most a bot can download from Telegram), 50 MB
and 20 files a message (OpenAI's 50 MB a request). Said before anything is
held.

**An album is one message.** Telegram hands a bot an album (up to 10 photos
or files sent as one message) as separate updates sharing a
`media_group_id`, and never says which is the last. So each piece is kept
(`AlbumItem`) and a check is queued 1.5 seconds after it; the check that
finds no newer piece sends them all on as one message, with the album's
caption as the question. The user sees nothing of this: one message, one
answer. A message with one file goes on at once. Files and photos sent
without a question get one in the user's language ("Summarize these
files.", "What is in these photos?"), so the answer is in it too. The sweep
forgets pieces whose check never ran.
- Every photo and file message now goes through the background
  (`flush_album`): a PDF is read there for its pages (`pypdf`, new
  requirement), so the hold covers them without slowing the webhook.
- Several voice notes or audio files in one album are heard together (one
  request, priced by their total length) and answered as one message.
- **Edit a photo** takes an album too: GPT Image gets every photo
  (`image[]`), for "put this product in this scene"; the summary shows
  "· 🖼 2".

**Files stay in the chat.** The photos and files of the messages the chat
remembers (Chat memory) go with every new message, newest first while they
fit one request; older ones, and any the current model cannot read, become
a marker ("[file: report.pdf]"). So "what does page 3 say?" works. They are
fetched from Telegram each time: nothing is stored on the server. This
replaces v0.1.0's "only the newest photo is sent again". A message keeps
its files (`Message.files`: name, type, size, pages; migration studio
0003).

**The hold** counts what the chat carries: 1,600 tokens a photo, 1,800 a
PDF page, three bytes a token for other files, on top of the chat's text
and the longest answer. The charge is still what OpenAI says it used.

**Safety.** Photos go through Moderation as before (one call a photo).
Document text is not moderated here; the chat model's own safety reads it,
as it reads any chat.

**Archive.** Every file sent in is copied to the archive channel, like
photos and voice notes.

**Tests.** 484 (up from 456): PDFs with their pages and hold, text, code
and Word files, images sent as files, unsupported files, videos, the 20 MB,
20-file and 50 MB limits, Claude's documents and refusals, files kept in the
chat and the oldest left out when they do not fit, the archive; albums of
photos and of files, waiting for the last piece and going once, a bad file
stopping the album, Edit a photo with an album, audio albums, stale pieces;
the Responses API (request, stream, length limit, unfinished and failed
streams) and Claude's documents.

**After deploy.** Nothing to set up.

### 2026-10-20 — v0.1.0 — Start AIstars: AI chat, images and voice, paid with Stars

The owner: a separate project, AIstars, where users get "chat, image creation
and … all possible features which come with AI API (default is OpenAI)";
"everything else stays the same like Telegram, like credits". Then: "do it
based on your idea and deploy on the server based on our rules". So the
choices proposed for confirmation were taken as made: start from a copy of
MyShortClip with the video parts removed; credits from the real cost of
each request; chat, images and voice first, the rest after.

**Why a copy of MyShortClip.** Its core (registration by phone, countries,
languages translated by AI, the one-panel bot, Stars credits with a clear
statement, credit requests, the safety checks, the spend guard, the archive
channel, the doctor, the deploy standard) is what "everything else stays
the same" means, and it is tested (568 tests at v0.14.0). The Higgsfield
creation app is left out whole; a new app, `studio`, holds the AI tools.
A fresh project, so fresh migrations: one schema migration per app, then the
seed data.

**What users get** (home: 💬 Chat · 🎨 Images / 🎙 Voice · 🧩 Assistants):
- **💬 Chat.** Anything typed is a chat message, unless the bot asked a
  question. A photo with a question, or a voice note, works too. The answer
  is written into one message as it comes (an edit every 1.5 s, as plain
  text), then formatted: the AI's Markdown becomes Telegram HTML, always
  balanced, split under 4,096 characters, and sent as plain text if
  Telegram refuses the HTML. Under it: what it cost, the model and the
  balance, **🔊 Listen · 🪙 X** and **➕ New chat**. The chat remembers its
  last messages (Runtime setting **Chat memory**, 20); only the newest photo
  is sent again. **🤖 Model** lists the chat models users can have, each with
  the price of an everyday answer; **🗂 My chats** reopens or deletes chats
  (deleting removes the messages, never the charges). `/new` starts a new
  chat.
- **🧩 Assistants.** Eight ready-made chats with their own instructions:
  Translator, Writer & editor, Emails & messages, Summarizer, Captions &
  hashtags, Code helper, Tutor, Brainstorm (AI tools → Assistants; each can
  have its own model).
- **🎨 Images.** Create an image from a description, or Edit a photo (send
  it, then say what should change). The summary has the shape (square,
  portrait, landscape; for an edit also "as the photo") and quality (quick
  draft, good, best) as buttons, and the most it can cost. The image comes
  with what it cost, **Again**, **Edit this** and, with the archive channel,
  the **Original file** (Telegram compresses photos).
- **🎙 Voice.** A voice note in a chat is turned into text ("🎙 …") and
  answered. **Voice to text** gives a voice note or audio file back as text
  (up to 20 minutes). **Text to speech** reads a text aloud (up to 4,000
  characters) in the voice the user chose (eight voices).

**Prices: held, then charged what it really cost.** Every request is a Job
(AI tools → Requests).
- When it starts, the most it can cost is held: a chat answer at the
  model's longest answer (its **Max output tokens**) with the chat so far; an
  image at its size and quality (GPT Image's published image tokens, half
  again to spare); a voice note by its length; a text read aloud by its
  characters.
- When it is done, what the provider reports it used is priced with the
  model's prices and charged, never more than the hold; the rest comes back.
  A request that fails is returned whole. Credits = cost ÷ Star value ×
  (1 + margin ÷ 100), rounded up, at least 1, as in MyShortClip; a model can
  have its own margin.
- The ledger's creation kinds became **hold**, **charge** (+ the rest of the
  hold) and **return** (+ all of it), once each per request (a database
  constraint, the account row locked). The statement shows each request as
  one line, with what it cost: "Chat answer #12 −3 → 🪙 97"; while it runs,
  "… · in progress, held".
- An image or voice note is delivered before it is charged: if Telegram
  takes it nowhere, nothing is charged. A sweep every five minutes returns
  the credits of a request that never finished (20 minutes) and tells the
  user. One chat answer at a time per chat; **Requests at once per user** (2).

**AI → Models: a use per model.** Chat, Images, Voice to text or Text to
speech, with the prices that use needs (per 1M tokens; images also per 1M
tokens of photos sent in; per minute of audio; per 1M characters read), a
tagline users read (translated like the bot's texts), whether it sees
photos, whether users are offered it, and one default per use. **Sync
models** (AI → Providers) reads the provider's list of models: new ones are
added switched off (no API publishes prices, so the owner sets them), and
an offered model the provider does not list is marked and named by the
doctor. Seeded, with OpenAI's prices read today from its pricing page:
- Chat: GPT-6 Luna (default; $0.10 / $0.50), GPT-6.1 Sol ($2 / $10), GPT-6
  Astra ($10 / $50). GPT-6.1 Sol's ID is confirmed; the other two follow
  the pricing page's names, which Sync models checks once the key is set.
  Claude Haiku 5.5 and Sonnet 5.5 are offered too, shown only with an
  Anthropic key.
- Images: GPT Image 2.5 Flare (default) and Sunburst ($5 text, $8 photos in,
  $30 image out).
- Voice to text: GPT Transcribe ($0.0045 a minute), GPT-4o mini Transcribe.
- Text to speech: GPT-4o mini TTS, $20 per 1M characters (about $0.015 a
  minute of speech, on the safe side).
- GPT-6 Luna also translates the bot and is the safety judge.

**Calls** (`apps/ai/clients.py`, plain HTTP as before): `chat` (streamed,
both providers, photos as data URLs or base64 blocks, UTF-8 forced on the
stream), `image` (generations, or edits with the photo), `transcribe`,
`speak` (Ogg Opus, a voice note Telegram plays), `list_models`. OpenAI's own
safety refusal is told apart (`moderation_blocked`). Every call passes the
spend guard with the most it can cost, and is recorded at what it cost.

**Safety, as in MyShortClip, with chat checked lightly.**
- Image requests: the blocklist, OpenAI Moderation, then the AI policy
  judge (it sees the photo), before the price is shown; the same policy and
  words as MyShortClip (real people and the user's own face included). A
  request OpenAI's own safety system refuses is a strike and a review too.
- Chat messages and texts read aloud: blocklist words marked **Chat** (none
  of the seeded image words, so "naked eye" is fine), and Moderation
  blocking only sexual content with minors, violent crime and threats.
  Self-harm is never blocked in a chat: the model answers with care.
- The judge has 2,000 tokens to answer: GPT-6 models reason before they
  answer, and 300 (MyShortClip's) could leave nothing.
- Fail closed, strikes, the review queue, the owner's notice and the archive
  copy are unchanged.

**Countries: OpenAI's supported list.** OpenAI warns that offering its API
outside its supported countries can get the account suspended. The seed
allows exactly its list (207 countries and territories, read today);
Russia, Belarus, China, Hong Kong, Iran, Cuba, North Korea, Syria,
Venezuela and the unlisted territories start not allowed. Iran stays
refused whatever happens. English, Romanian and Russian are offered;
Romanian and Russian are translated by AI once the key is set.

**Kept from MyShortClip, renamed:** Demo mode (on by default: every tool
answers without any provider, testers only, credits as usual) and
**Everyone can use the tools** (was "Everyone can create"); the archive
channel (now photos and voice notes in, images and voice notes out); Runtime
settings; the doctor (an **AI tools** section: who may use them, the models
per use, assistants, the archive, stuck requests). Removed with the video
parts: card panels (`Account.ui_media`), the old reply-keyboard menu and the
☰ command clean-up, which this bot never had.

**Deployment** (standard items 1–14): repo `fath1993/aistars`, domain
`aistars.myshortclip.com` (a subdomain of a zone already in Cloudflare),
project `aistars`, network `aistars-net`, `/var/www/aistars`, pgAdmin on
127.0.0.1:8131. No app port on the host: the edge reaches the project nginx
directly. A new `.env` with its own secrets. The Celery worker runs eight
at once (chat answers wait on the network, not the CPU).

**Tests.** 456, without network: the chat through the webhook (streaming,
the hold and the charge, memory, photos, long and refused answers,
failures and Try again, blocks, limits, Demo, models, chats, assistants,
Listen), images (the three checks, options, Create, the archive and the
original, Again, edits, OpenAI's refusal, failures), voice (a voice chat,
voice to text, text to speech, voices), the ledger and statement, the
sweep, the formatter (balanced HTML, splitting), the clients (streams of
both providers, images, audio, sync), the lighter chat check, the archive,
the doctor, and everything kept from MyShortClip.

**After deploy.**
- First deploy: deploy/README.md, "First deploy" and "One-time setup" (the
  Cloudflare record, the clone, the deploy key and its three secrets).
- Runtime settings: the bot token (from @BotFather), Public base URL
  `https://aistars.myshortclip.com`, Your Telegram ID, Support contact.
  Then Control panel → System status & webhook → **Register the Telegram
  webhook**.
- In the bot: /start, share the number; in the admin: your account → action
  **Tester**, and **Adjust credit** to try everything in Demo mode.
- Going live: AI → Providers → OpenAI: paste the key, **Test**, **Sync
  models**; Control panel: untick **Demo mode**; tick **Everyone can use the
  tools** when ready. Optionally a private channel with the bot as admin,
  from your account, as the archive.

**Next** (proposed, not built): questions about documents (PDF and text
files), answers with web search, video (Sora), and a welcome gift of
credits.

## Requests

Requests that changed no files, newest first.

- 2026-10-21 — The archive channel is not recorded; a message posted in it did not reach the admin — The bot is not sent channel posts (allowed updates: message, callback_query, pre_checkout_query, my_chat_member), so posting there does nothing; it records the channel only from Telegram's notice that it was made admin, from the account in Your Telegram ID (archive_refused in the Activity log otherwise). Sent the manual way (the channel's -100… ID from web.telegram.org/a or @userinfobot into Runtime settings → ARCHIVE_CHAT_ID) and offered a sturdier setup: post a code shown in the admin in the channel.
- 2026-10-21 — Server logs after v0.4.2 — The nginx log confirmed the cause: "connect() failed (111: Connection refused) ... upstream: http://172.25.0.7:8000", the app's old address, from an nginx started before the app was recreated. The v0.4.2 deploy itself still ran the old dp.sh (bash had read it before git replaced it), so nginx was not recreated; a manual Run workflow did it, and the admin login now loads at aistars.myshortclip.com. The deploy stays red until the bot token and public base URL are set (doctor --strict).
- 2026-10-21 — The app is not live after the server setup — aistars.myshortclip.com answers with nginx's "Welcome to nginx!" page while platform.myshortclip.com works: the request reaches a host nginx default site, not the edge (Traefik) that routes to the project's own nginx by its labels, so the server-edge cutover (README, one-time cutover) has most likely not been done on the server. Nothing in the repo routes to that page. Sent the check (`ss -ltnp` on port 80, the running containers) and the cutover steps; the red deploy run is doctor --strict failing until the bot token and webhook are set in the admin.
- 2026-10-21 — The GitHub repo fath1993/aistars is created — Pushed main (v0.1.0 to v0.4.1). The first Actions run passed the tests and the missing-migration check; the deploy job stopped at "Check the deploy secrets are set", waiting for the server setup (Cloudflare record, clone, dp.sh, deploy key and secrets).
- 2026-10-20 — How chat history is handled — Explained it as built: the last 20 messages of a chat go with each new one, older ones are dropped silently; photos and files go again with every follow-up while they fit; chats are kept until the user deletes them, and deleting one leaves the user's words in the request record and their files in the archive channel. Proposed (for confirmation): a token budget with an AI-written chat memory of older messages, open files shown with what they add to each message and closed after a few messages, providers' prompt caching passed on in the price, AI titles, search and export of chats, start fresh with the memory, an About me sent with every chat, deleting a chat removing its words and files everywhere, automatic deletion of chats unused for a set time, Delete all my chats, a /privacy page; asked about retention, the owner reading chats in the admin, and About me.
- 2026-10-20 — Choose the kind of assistant when starting a chat (predefined agents: lawyer, teacher, translator, programmer…), from a catalog with categories and search; every request's cost and usage shown; free navigation everywhere; make the idea richer — Plan sent for confirmation (v0.3.0): an agent catalog of about 40 agents in 10 categories, each with a card (what it does, starter questions to tap, its model and everyday price), keyword search plus a free AI "find the right agent" with a daily cap, My agents, recent and popular, share links (t.me/…?start=agent_…); New chat starts from the catalog; cost under every answer with a Details button (tokens, files, hold returned), a confirmation above a set price, a low-balance warning, a Usage page and an optional daily limit; home rebuilt around New chat, Continue, Agents; commands /new /agents /chats /usage; admin categories, counters and AI-drafted agents. Later: users' own agents, agents with web search.
- 2026-10-20 — Support every kind of file the AI API takes (images, voice, PDFs, Office files...), and several files or photos at once — Checked OpenAI's file inputs: the Responses API reads PDFs (text and page images), Word, Excel/CSV, PowerPoint, text and code, up to 50 MB a request; Chat Completions reads only PDFs. Plan sent for confirmation: OpenAI chat moves to the Responses API; files and photos sent without a question wait as attachments (an album is collected into one message) until the user types a question or taps Send as is, up to 10 files, 20 MB each (Telegram's bot limit); files stay in the chat for follow-up questions while within the chat memory; the hold covers the files' size; Claude models read PDFs, text and photos; several photos for Edit a photo; video not yet.
- 2026-10-20 — AIstars as a separate project: chat, images and every AI API feature (OpenAI by default), everything else as in MyShortClip — described the plan (start from MyShortClip's code; chat, images, voice, documents, web search, video, deep research and ready-made assistants; credits from the real cost) and asked four questions; the owner answered "do based on your idea", which became v0.1.0.
- 2026-10-20 — Add AI chat (OpenAI, Anthropic) to MyShortClip for extra value — described a chat module inside MyShortClip (models per provider, token billing on the same wallet, safety, history, a prompt assistant feeding video); the owner chose a separate project instead.
