# Daily Philosophy reels

Automatic Bhagavad Gita reels in Hindi for [@dailyphilosophyforyou](https://www.instagram.com/dailyphilosophyforyou/).
Everything runs on your own Mac: it renders the videos, keeps them in a local
folder, and posts them through Instagram's official API on a schedule that
starts at **2 reels a day** and steps up to **10 a day** by week 7.

GitHub holds the backlog of **1000 reel recipes** (`reels/batch-01.yaml` to
`batch-10.yaml`) and the quote bank they come from ([QUOTES.md](QUOTES.md)).
The MP4 files are made on your Mac from those recipes and never go into git.

```
content/quotes.yaml     121 Bhagavad Gita verses (English) with ids and verse numbers
content/quotes_hi.yaml  the same verses in simple modern Hindi (what the reels show)
content/topics_hi.yaml  Hindi hooks, reflection questions and calls to action
content/archive/        the earlier mixed-philosophy quote bank (not used)
content/topics.yaml     hooks, reflection questions and hashtags per topic
reels/batch-*.yaml      1000 reels: on-screen text, timing, colour theme, caption
config.yaml             schedule, ramp, posting window, render settings
philoreels/             the code (render, post, schedule)
assets/                 background pictures, music and fonts on your Mac (not committed)
~/PhiloReels/           on your Mac: rendered videos, token, posting log
```

## How posting works

A background job (launchd) runs `philoreels tick` every 10 minutes. Each run:

1. Works out today's number of reels from the ramp (2, 3, 4, 5, 6, 8, then 10 a day by posting week).
2. Spreads them evenly between 07:00 and 23:00, shifted a few minutes each day.
3. If a slot has passed and nothing was posted for it, renders the next reel if needed and posts it:
   create a reel container, upload the MP4 straight from the Mac, wait for Instagram to process it, publish.
4. Otherwise renders a couple of reels ahead so the next ones are ready.

If the Mac was asleep, missed slots are caught up one at a time, at least 40 minutes apart.
After 3 failures in a row it pauses itself and shows a Mac notification.

## Setup (once)

### 1. Install on the Mac mini

```bash
brew install ffmpeg python libraqm   # libraqm shapes the Hindi (Devanagari) text
git clone https://github.com/August85/Instagram-Philosophy.git ~/code/Instagram-Philosophy
cd ~/code/Instagram-Philosophy
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/philoreels fetch-images        # downloads ~40 free rural India pictures with no faces
.venv/bin/philoreels make-music          # makes 8 flute-and-tanpura tracks
.venv/bin/philoreels preview --open      # renders reel 0001 and opens it
```

In **System Settings > Energy**, turn on "Prevent automatic sleeping when the display is off",
so the Mac stays awake to post.

### 2. Connect Instagram

Posting a video file straight from your Mac needs Instagram's API with **Facebook Login for Business**.

1. In the Instagram app, switch @dailyphilosophyforyou to a **professional account**
   (Settings > Account type and tools > Switch to professional account). Creator or Business both work.
2. Create a **Facebook Page** for the account and link it: in the Instagram app go to
   Settings > Accounts Center, or on Facebook go to Page settings > Linked accounts > Instagram.
3. Go to [developers.facebook.com](https://developers.facebook.com/apps), create an app of type **Business**,
   and add the **Instagram** product (choose "API setup with Facebook login").
4. Copy the **App ID** and **App secret** (App settings > Basic) into a `.env` file:
   ```bash
   cp .env.example .env    # then fill in FB_APP_ID and FB_APP_SECRET
   ```
5. Open the [Graph API Explorer](https://developers.facebook.com/tools/explorer/), pick your app,
   choose **Get User Access Token**, and tick these permissions:
   `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `business_management`.
   Generate the token and copy it.
6. Save it (it is swapped for a 60-day token and then refreshed automatically):
   ```bash
   .venv/bin/philoreels auth --token "PASTE_TOKEN" --username dailyphilosophyforyou
   .venv/bin/philoreels check
   ```

Because you are the app's admin posting to your own account, the app can stay in development mode.
App Review is only needed if other people's accounts will use it.

### 3. Go live

```bash
.venv/bin/philoreels post --dry-run          # renders the next reel and shows its caption
.venv/bin/philoreels start                   # week 1 (2 a day) starts today
.venv/bin/philoreels install-schedule        # background job every 10 minutes
.venv/bin/philoreels status
```

Logs are in `~/PhiloReels/logs/tick.log`.

## Day to day

| Command | What it does |
| --- | --- |
| `philoreels status` | Today's slots, how many went out, backlog left, last 5 posts |
| `philoreels pause` / `resume` | Stop or restart automatic posting |
| `philoreels cap 4` | Hold at 4 a day even if the ramp says more (`cap 0` removes it) |
| `philoreels post` | Post the next reel right now |
| `philoreels render --count 50` | Render the next 50 reels ahead of time (about 5 to 10 s each on an M-series Mac) |
| `philoreels preview --id 0042 --open` | Re-render one reel and open it |
| `philoreels quotes --unchecked` | List quotes whose wording still needs checking |
| `philoreels quotes --markdown` | Rebuild QUOTES.md after editing the quote bank |
| `philoreels generate` | Rebuild the 1000 recipes after editing quotes or topics |
| `philoreels uninstall-schedule` | Remove the background job |

Check reach every Monday. If views per reel fall for two weeks after a step up, hold the volume with `philoreels cap`.

## Making the reels better

- **Pictures:** `philoreels fetch-images` downloads free pictures and illustrations of rural India (villages,
  fields, rivers) from Openverse and Wikimedia Commons, plus Pixabay if `PIXABAY_API_KEY` is in `.env`. Only CC0,
  public-domain and Pixabay-licence pictures are kept, so no credit or fee is owed, and the Mac's built-in face
  detection drops any picture that shows a human face. Sources are listed in `assets/backgrounds/CREDITS.md`, and a
  contact sheet of all pictures is saved to `~/PhiloReels/backgrounds_contact_sheet.jpg`. Change what is searched
  for under `images.queries` in `config.yaml`. Delete any picture you don't like, or add your own. Each picture is
  shown whole over a blurred copy of itself with a slow zoom.
- **Music:** `philoreels make-music` writes 8 calm flute-and-tanpura tracks to `assets/music/`. They are made on
  your Mac, so there is no copyright claim on them. You can add or swap in royalty-free tracks (for example
  from Pixabay Music). Reels posted through the API cannot use Instagram's music library, so music is part of
  the video. Volume is `render.music_volume` in `config.yaml`.
- **Language:** `content.language: hi` in `config.yaml` puts the text, caption and voice in Hindi; `en` switches
  back to English. Run `philoreels generate` after changing it.
- **Voiceover:** on by default. Every line on screen is read aloud except the verse reference, which is only
  shown (`render.voice_reads_reference: true` reads it too). Each line stays up until it has been read, and the
  music drops to `render.music_volume_with_voice` under the voice. The default engine is the Mac's own Hindi voice,
  Lekha; download "Lekha (Enhanced)" in System Settings > Accessibility > Spoken Content > System voice > Manage
  Voices for a more natural sound. Try a voice with `philoreels voice-test --play`.
- **Your own voice (ElevenLabs):** make a voice clone in ElevenLabs, then on the Mac:
  1. Put `ELEVENLABS_API_KEY=...` in `.env` (ElevenLabs > Developers > API keys).
  2. Run `philoreels voices` and copy your clone's id into `render.elevenlabs.voice_id` in `config.yaml`.
  3. Set `render.tts: elevenlabs`, then run `philoreels voice-test --play` to hear it.
  The `eleven_multilingual_v2` model speaks Hindi. Each line's audio is saved in `~/PhiloReels/voice-cache`, so a
  verse is only paid for once even though it appears in several reels. All 1000 reels need only about 15,000 characters
  in total (244 different lines). Delete already rendered videos (`~/PhiloReels/videos`) so they are made again with the new voice.
- **Quotes:** edit `content/quotes_hi.yaml` (and `content/quotes.yaml` for new verses), set `checked: true` once a quote's wording is verified, then run
  `philoreels generate` and `philoreels quotes --markdown`. Reels already posted are tracked by id in
  `~/PhiloReels/posted.jsonl`, so regenerate only before going live or add new reels after the last id.
- To skip a single reel, add `skip: true` to it in its batch file.

## Notes

- The verses are our own simple Hindi and English renderings, not a published translation, so they carry no
  copyright. Each is marked `checked: false` until you have compared it with a translation you trust.
- Instagram allows up to 100 API posts per 24 hours; this pipeline never posts more than 25 a day.
- Run the tests with `.venv/bin/pip install -e ".[dev]" && .venv/bin/pytest -q`.
