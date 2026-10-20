# Deployment

## First deploy

1. Clone into `/var/www/aistars` as the `ubuntu` user:

   ```bash
   cd /var/www && sudo -u ubuntu git clone git@github.com:fath1993/aistars.git
   ```

2. `aistars/.env` comes with the repo, already filled in. Check that
   `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` name your domain, and that the
   same domain is in the `Host(...)` label of the `nginx` service in
   `docker-compose.yml` (a test fails if they differ).
3. Add the domain in Cloudflare as a proxied A record to the server.
   Cloudflare does HTTPS (SSL mode Flexible); the server's edge proxy
   (`server-edge` repo) routes it here. No nginx file, no certificate.
4. `sudo ./dp.sh`
5. `docker compose exec aistars python manage.py createsuperuser`
6. In the admin, open **Runtime settings** and set the bot token and the
   public base URL. The webhook secret is already filled in from `.env`.
7. Open **Control panel → System status & webhook** and press
   **Register the Telegram webhook**. Every check should then be OK.
8. Send `/start` to the bot, share your number, then in the admin: Accounts →
   your account → action **Tester**, and **Adjust credit** to try it in Demo
   mode.
9. When you are ready to go live: AI → Providers → OpenAI → paste the key,
   **Test**; Control panel → untick **Demo mode**; tick **Everyone can use
   the tools** to open it.

After that, deploys are automatic (below). `sudo ./dp.sh` still works by hand;
it ends by running `manage.py doctor --strict`, which prints the fix for
anything not working, and exits non-zero if something is.

## Automatic deploys

Every push to `main` runs `.github/workflows/deploy.yml`: the tests first,
then, only if they pass, an SSH connection to the server that runs `dp.sh` on
exactly that commit. **Actions → Test and deploy → Run workflow** redeploys
`main` without a new commit.

The GitHub key can run one thing only: `deploy/ci-deploy.sh`, which accepts a
commit ID and nothing else. A leaked key can only redeploy a commit on `main`.

Deploys reset the server to the repository, so change `.env` in the repo,
never on the server: a server-side edit is overwritten by the next deploy.

### One-time setup

1. Pull the version that contains `deploy/ci-deploy.sh` once by hand:

   ```bash
   cd /var/www/aistars && sudo ./dp.sh
   ```

2. On the server, make the deploy key and print the three secret values:

   ```bash
   sudo bash /var/www/server-edge/setup-deploy-key.sh aistars
   ```

   It locks the key in root's `authorized_keys` to `deploy/ci-deploy.sh`,
   replaces this project's previous key, and leaves no private key on the
   server: the printed one is the only copy. `DEPLOY_HOST` is the IP, not
   the domain (SSH through Cloudflare fails); the known-hosts line is read
   from the server's own host key file, so it cannot be spoofed in transit.

3. In GitHub: **Settings → Secrets and variables → Actions → New repository
   secret**, once per value, named exactly `DEPLOY_HOST`, `DEPLOY_SSH_KEY`,
   `DEPLOY_KNOWN_HOSTS`. Paste the whole private key, including the
   `-----BEGIN` and `-----END` lines.

4. **Actions → Test and deploy → Run workflow.** Both jobs should go green.

If the deploy job fails with `Permission denied (publickey)`, sshd refuses
root logins: `PermitRootLogin` in `/etc/ssh/sshd_config` must be
`prohibit-password` (Ubuntu's default) or `yes`.

## Running the tests

Locally:

```bash
python manage.py test --settings=aistars.c_settings.test
```

On the server, inside the running container:

```bash
docker compose exec aistars python manage.py test --settings=aistars.c_settings.test
```

## Web traffic

```
visitor ⇄ HTTPS ⇄ Cloudflare ⇄ HTTP ⇄ edge (server-edge, Traefik, :80) → nginx container → aistars:8000
```

- `deploy/nginx/aistars.conf` is the project's nginx: static files from
  `/var/www/aistars/static` (read-only), everything else to the app.
- The `nginx` service's labels in `docker-compose.yml` tell the edge which
  domain is ours. Changing the domain means changing that label and
  `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` in `.env`.

## The menu

- **Inline buttons only.** The home panel is the menu. Every other screen
  ends with **‹ Back** · **🏠 Menu**; a question (a description, a photo, a
  text to read) has **✖️ Cancel**. Messages that are not the panel (an
  answer, an image, a voice note, a notice, a receipt, /terms, /support,
  /paysupport) have **🏠 Menu**: the message stays and the menu is sent under
  it. Their other buttons (`out:` callbacks) never turn them into the panel.
- **Anything typed is a chat message**, unless the bot asked a question.
  Commands work when typed (`/start`, `/new`, `/credits`...); there is no ☰
  Menu button and nothing under the typing field.

## Languages, AI and regions

- **Texts:** English, written in the code as `_("...")`. Every deploy records
  them (Admin → **Texts**); new ones are translated in the background. The
  database's own texts users read (model taglines, assistants) are recorded
  the same way whenever they are saved.
- **Languages:** Admin → **Languages** lists every language of the world.
  **Used**: offered to users (Profile → Language). **Translate**: kept
  translated by AI; ticking it starts the translation, and the page shows the
  percentage and status. Open a language to fix any text by hand (kept on
  retranslation). Actions: translate missing texts now, or retranslate
  everything with the model chosen next to the action menu.
- **AI:** Admin → **AI** → **Providers**: paste the OpenAI (and, if you like,
  Anthropic) API key, then the **Test** action. **Sync models** reads the
  provider's list: new models are added switched off (set their prices, then
  offer them), and a model the provider does not list is marked. **Models**:
  each model's use (Chat, Images, Voice to text, Text to speech), its
  tagline, its prices, whether users are offered it and which is their
  default, and the defaults for translation and safety. Every call counts
  against the Control panel's USD caps and kill switch.
- **Registration:** new users share their own phone number with Telegram's
  contact button before anything else works.
- **Countries:** Admin → **Countries** decides by the number's country who may
  use the bot, and which language each country gets by default. They start
  as OpenAI's supported countries: offering its API elsewhere can get the
  account suspended. Iran is never allowed.

## Credits and payments

- **Credits:** one credit is one Telegram Star. Users buy packs (Admin →
  **Billing → Packs**) with a Stars invoice; Telegram asks the bot to confirm
  each payment first, so press **Register the Telegram webhook** once after
  the first deploy.
- **Requests:** the most a request can cost is held when it starts (a chat
  answer at the model's longest length, an image at its size and quality);
  when it is done, what the provider says it really cost is charged and the
  rest comes back; a request that fails costs nothing. The statement shows
  each request as one line, with what it cost.
- **Ledger:** every credit movement, never edited or deleted (Billing →
  Ledger, CSV export). A user's balance is their newest row.
- **Refunds:** Billing → Payments → Refund. Refused once the user has spent
  the credits.
- **Credit requests:** Billing → Credit requests → Approve (any amount, next
  to the action menu) or Reject; the user is told in the bot. Set **Your
  Telegram ID** in Runtime settings to be told of new requests.
- **Pricing:** Runtime settings → **Profit margin (%)** and **Value of one
  Star (USD)**; a model can have its own margin. Price in credits = cost ÷
  Star value × (1 + margin ÷ 100), rounded up, at least 1.
- **Support:** Runtime settings → **Support contact**, shown by `/support` and
  `/paysupport` (Telegram requires the latter for bots that take Stars).

## The AI tools

- **Who:** accounts marked **Tester** (Accounts → action Tester), and
  everyone once Control panel → **Everyone can use the tools** is ticked
  (with Demo mode off; Demo stays with the testers).
- **Demo mode** (Control panel, on by default): every tool answers without
  calling any provider (a demo answer, a sample image, a sample voice note);
  credits are held and charged as usual. Off: each model uses its provider.
- **💬 Chat:** type, or send photos, files or a voice note, with the
  question as the caption. The AI reads photos, PDFs (text and page images),
  Word and other documents, Excel/CSV (the first 1,000 rows a sheet),
  PowerPoint, text and code (OpenAI's models; Claude's read PDFs, text and
  photos); at most 20 MB a file (Telegram's limit for bots), 50 MB and 20
  files a message. An album (several photos or files sent as one message) is
  one question with one answer. Files stay in the chat while it remembers
  their message, so follow-up questions work. The answer is written into one
  message as it comes, then formatted, with what it cost. Users choose the
  model (each with the price of an everyday answer), start new chats with
  the General assistant or an agent, reopen or delete old ones (**My
  chats**).
  **🔊 Listen** under an answer reads it aloud. Runtime settings: **Chat
  memory** (earlier messages sent with each new one), **Longest message**,
  **Requests at once per user**.
- **🧩 Agents:** AI assistants made for one job, each with its own
  instructions (in English; it answers in the user's language), starter
  questions users can tap, and optionally its own model. 39 to start with, in
  10 categories (AI tools → **Categories** and **Agents**). Users browse by
  category, search by words (name, line and **search words**, in English and
  their language), or tap **✨ Find for me** and describe their need (the
  model marked Default for translation picks; free for users, **Find for me,
  per user a day** in Runtime settings). Each agent has a card (what it does,
  its questions, its model and everyday price, ▶️ Start, ⭐ Save, 📤 Share
  with a link `t.me/<bot>?start=agent_<slug>` that opens the card, even for
  someone who still has to register). **Featured** agents are offered first
  in ➕ New chat. The Agents list shows each one's chats and credits charged.
  Category → action **Draft 3 new agents with AI** writes agents for it,
  switched off, for you to read and switch on.
- **What things cost:** every answer, image and voice result ends with what
  it cost and the balance; **🧾 Details** shows the model, agent, tokens and
  the hold (charged, returned). A chat message that can cost more than **Ask
  before sending above** (Runtime settings, 🪙 20) waits for **Send**; once
  agreed in a chat, only one that can cost half as much again asks. Below
  **Low balance warning**, results say so with a Buy credits button.
  **📊 Usage** (home, `/usage`) shows today, 7 and 30 days, by tool, agent and
  model, the last requests, and a daily limit users may set themselves.
- **Commands:** `/new`, `/agents`, `/chats`, `/usage`, `/images`, `/voice`,
  `/menu`, `/help` (typed; there is no ☰ command list).
- **🎨 Images:** Create an image from a description, or Edit a photo (or
  several, sent as one album). Shape
  and quality are buttons; the summary shows the most it can cost. The image
  comes with **Again**, **Edit this** and, with the archive channel, the
  **Original file**.
- **🎙 Voice:** a voice note in a chat is turned into text and answered;
  **Voice to text** gives the text back; **Text to speech** reads a text aloud
  in the voice the user chose.
- **Archive channel:** set **Your Telegram ID**, then, from that account,
  make the bot an admin of a private channel that it may post in. It is
  recorded by itself (Runtime settings → Archive channel). Every photo and
  voice note sent in, and every image and voice note made, is copied there.
- **Requests:** AI tools → **Requests**: each one's hold, cost and charge.
  **Fail and return the credits** ends one that is stuck; a sweep every five
  minutes does it by itself after 20 minutes.

## Safety

- **Image requests** are checked when their summary is first shown, before
  the price: the blocklist, OpenAI Moderation, then the AI policy judge (it
  sees the photo). The first block stops it; a check that cannot run means
  nothing is made.
- **Chat messages and texts read aloud** get the lighter check: blocklist
  words marked **Chat**, and Moderation blocking only its clearest harms
  (sexual content with minors, violent crime, threats). Self-harm is never
  blocked in a chat; the model answers with care.
- **Set up:** AI → Providers → OpenAI key (Moderation is free); the judge is
  AI → Models, **Default for safety** (GPT-6 Luna to start). Outside Demo
  mode the judge is required.
- **Edit:** Safety → **Blocked words** (whole words, any language; tick Chat
  for words that should stop chat messages too) and Safety → **Policy**
  (what the judge allows and blocks in images).
- **Blocks:** the user sees the category only and pays nothing. Each block,
  and each request the provider's own safety system refuses, is a strike, is
  copied to the archive channel, and is told to **Your Telegram ID**. At
  **Strikes before blocking** (Runtime settings, 3) the account is blocked.
  Safety → **Review queue**: Confirm, or take the strike back (unblock the
  account in Accounts if needed).

## Ports

`8131` (pgAdmin) on the host, bound to 127.0.0.1; reach it through an SSH
tunnel. The app publishes no port: the edge reaches its nginx over the
project network. Override with `AIS_PGADMIN_PORT`.
