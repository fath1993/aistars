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
6. **Project nginx.** Each web project has its own nginx container, configured in `deploy/nginx/` in its repo. It serves static files read-only from `/var/www/<project>/static` and public media read-only from the project's media folder, and proxies the rest to the app, passing `X-Forwarded-Proto $http_x_forwarded_proto`, never `$scheme`. Files the app serves itself (WhiteNoise static, media only admins may see) stay with the app.
7. **Routing.** The project nginx carries the edge labels: `traefik.enable=true`, `traefik.docker.network=<project network>`, `Host(<domain>)`, entry point `web`, port 80. The domain equals `ALLOWED_HOSTS` in `.env`, checked by a test. A new domain needs only a proxied Cloudflare DNS record.
8. **Deploy script (`dp.sh`).** Runs as root. Deploys a given commit, refused unless it is on `main`, or the latest `main`. Rebuilds the containers (the app migrates on start), fixes ownership and permissions, waits for migrations, runs `doctor --strict`, and exits non-zero if it fails. A project without a database waits for the app to answer instead of for migrations.
9. **CI/CD.** `.github/workflows/deploy.yml`: on every push to `main`, the tests and (Django) the missing-migration check, then a deploy of that exact commit over SSH. One deploy at a time; **Run workflow** redeploys.
10. **Deploy key.** One SSH key per project, made on the server with `setup-deploy-key.sh <project>` from `server-edge`. GitHub secrets `DEPLOY_HOST`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`. Locked in root's `authorized_keys` to the project's `deploy/ci-deploy.sh`, which accepts only a commit ID. The host key is pinned, and the private key is deleted from the server after setup.
11. **Health.** `doctor` (`manage.py doctor`, or `doctor.py` without Django) checks the configuration and every external service the app needs, and prints the fix for each failure. Where the admin has a System status page, it shows the same checks. Every new external service or required setting gets a check.
12. **Tests.** Run in CI without external services (Django: the test settings, SQLite in memory; without Django: pytest), including the log and version consistency test, the domain test (item 7) and a minimum test count that only goes up.
13. **Secrets.** `.env` stays out of Docker images (`**/.env` in `.dockerignore`) and is `0640` on the server. No secrets in logs.
14. **New project.** Copy `Dockerfile`, `docker-compose.yml`, `dp.sh`, `deploy/ci-deploy.sh`, `deploy/nginx/`, `.github/workflows/deploy.yml` and `deploy/README.md` from the closest project (MyShortClip for Django, SpotFarsi FastAPI without Django). Rename the project, network, paths and domain. Then add the proxied Cloudflare DNS record, clone the repo into `/var/www/<project>` on the server, and set up its deploy key (item 10).
15. **Firewall.** The server sits behind a Hetzner Cloud Firewall (`FIREWALL.md` in `server-edge`): SSH (22) open, HTTP (80) only from Cloudflare's IP ranges, everything else closed, so the server's IP answers nothing on its own. A project never needs a firewall change.

## Changelog

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

- 2026-10-20 — Choose the kind of assistant when starting a chat (predefined agents: lawyer, teacher, translator, programmer…), from a catalog with categories and search; every request's cost and usage shown; free navigation everywhere; make the idea richer — Plan sent for confirmation (v0.3.0): an agent catalog of about 40 agents in 10 categories, each with a card (what it does, starter questions to tap, its model and everyday price), keyword search plus a free AI "find the right agent" with a daily cap, My agents, recent and popular, share links (t.me/…?start=agent_…); New chat starts from the catalog; cost under every answer with a Details button (tokens, files, hold returned), a confirmation above a set price, a low-balance warning, a Usage page and an optional daily limit; home rebuilt around New chat, Continue, Agents; commands /new /agents /chats /usage; admin categories, counters and AI-drafted agents. Later: users' own agents, agents with web search.
- 2026-10-20 — Support every kind of file the AI API takes (images, voice, PDFs, Office files...), and several files or photos at once — Checked OpenAI's file inputs: the Responses API reads PDFs (text and page images), Word, Excel/CSV, PowerPoint, text and code, up to 50 MB a request; Chat Completions reads only PDFs. Plan sent for confirmation: OpenAI chat moves to the Responses API; files and photos sent without a question wait as attachments (an album is collected into one message) until the user types a question or taps Send as is, up to 10 files, 20 MB each (Telegram's bot limit); files stay in the chat for follow-up questions while within the chat memory; the hold covers the files' size; Claude models read PDFs, text and photos; several photos for Edit a photo; video not yet.
- 2026-10-20 — AIstars as a separate project: chat, images and every AI API feature (OpenAI by default), everything else as in MyShortClip — described the plan (start from MyShortClip's code; chat, images, voice, documents, web search, video, deep research and ready-made assistants; credits from the real cost) and asked four questions; the owner answered "do based on your idea", which became v0.1.0.
- 2026-10-20 — Add AI chat (OpenAI, Anthropic) to MyShortClip for extra value — described a chat module inside MyShortClip (models per provider, token billing on the same wallet, safety, history, a prompt assistant feeding video); the owner chose a separate project instead.
