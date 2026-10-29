# Hype Atlas Trend Bot: full project report (handoff)

_Last updated: 2026-10-20. Written so any AI tool or new session (VS Code, Antigravity, Claude, etc.) can understand the
whole project from this one file. No secrets are stored here (all keys live only in GitHub Secrets)._

---

## 1. What this is

A fully automatic poster for the Facebook Page **Hype Atlas - Viral & Trending**
(facebook.com/hypeatlasnews) and its Instagram account **@hypeatlas2026**.
It collects trending news, writes posts with free AI models, renders posters and 15-17 second Reels with a voiceover,
and publishes 7 times a day. It runs on **GitHub Actions** (the owner's PC can be off).

- Repo: `github.com/Sohaib1056/trend-bot` (private), branch `main`. Language: Python 3.12.
- Live since **2026-10-04** (`DRY_RUN=false`).
- Instagram cross-posting since **2026-10-15**.

## 2. The owner and how to work with them (important)

- **Sohaib**, Pakistan, beginner. Reply in **simple Roman Urdu + English**, short and step by step.
- **Never ask for API keys or tokens in chat.** They go only into GitHub Secrets, and the owner pastes them.
- **Explain changes and get "haan" (yes) before commit or push.** A direct request ("ye kar do") counts as approval.
- **Always test with a dry run** (workflow input `dry_run=true`) before going live.
- **Give times in US time (New York ET) only, never PKT.** Exception: cron-job.org is set in Asia/Karachi, so give the
  cron values but explain them in ET.
- The assistant must not create accounts, type passwords or do public uploads for the owner. The owner does those.
- **Owner's content rules:**
  - Never cancel a post (a slot must never be empty), but the image must show the person or story.
  - No duplicate stories.
  - Facebook-safe wording.
  - Reels 15 s, at most 17 s.
  - Every Reel scene must be real and match what is said (no AI pictures).

## 3. Accounts and IDs (not secret)

| Thing | Value |
|---|---|
| Facebook Page ID | 1397517856769204 (profile id 61594808653327) |
| Meta app | "Hype Atlas Bot", id 1869241684446212, Published/Live |
| Meta app use cases | "Manage everything on your Page" + "Manage messaging & content on Instagram" (API setup with Facebook login) |
| Token permissions | pages_show_list, pages_read_engagement, pages_read_user_content, pages_manage_posts, pages_manage_engagement, read_insights, instagram_basic, instagram_content_publish |
| Page token | Never expires (Access Token Debugger: "Expires: Never"). Breaks only on FB password change, app removal, logout or admin loss. |
| Instagram | @hypeatlas2026, IG user id 17841450180672907 (Business account linked to the page) |
| Gmail for the brand | hypeatlas1@gmail.com |
| Scheduler | cron-job.org job "Hype Atlas Bot" (Asia/Karachi): `5 0,2,3,4,6,8,9 * * *`, POSTs a workflow_dispatch with a fine-grained GitHub token (Actions write, this repo only) |

**How the page token was made.** `me/accounts` is empty for this user, so:
1. Generate a user token in Graph API Explorer.
2. Extend it with the Access Token Debugger ("Extend Access Token").
3. Query `1397517856769204?fields=access_token` with the extended token. The result is the page token.
4. Put it in the `FB_PAGE_TOKEN` secret.

### GitHub Secrets (names only)

`GEMINI_API_KEY`, `GROQ_API_KEY`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`, `PIXABAY_API_KEY`, `FB_PAGE_ID`, `FB_PAGE_TOKEN`,
`IG_USER_ID`. Optional and unused: `PEXELS_API_KEY` (Pexels stopped issuing keys), `POLLINATIONS_API_KEY`,
`THREADS_USER_ID`, `THREADS_TOKEN`.

### GitHub Variables

`PAGE_NAME`, `NICHE`, `DRY_RUN=false`, `AUTO_REPLY=on`. Optional: `COUNTRIES`, `POSTS_PER_RUN`, `AVOID_POLITICS`,
`POST_FORMAT`.

## 4. Schedule (7 posts a day)

The slots are fixed in **UTC** (`bot/config.py`). New York is UTC-4 until DST ends on 1 Nov 2026, then UTC-5.

| UTC | ET (now) | Type |
|---|---|---|
| 19 | 3:05 PM | Reel (star slot) |
| 21 | 5:05 PM | Reel (star slot, the best slot) |
| 22 | 6:05 PM | Reel (was the trending slot; Elon since 10-29) |
| 23 | 7:05 PM | Photo (moved from 10:05 PM on 10-19; 10 PM was the weakest slot) |
| 01 | 9:05 PM | Reel |
| 03 | 11:05 PM | Reel |
| 04 | 12:05 AM | Photo |

- `POST_SLOTS=1,3,4,19,21,22,23`, `PHOTO_SLOTS=4,23`, `STAR_SLOTS=19,21`, `TREND_SLOTS` empty (was 22 until 10-29).
- **Owner, 10-29: ONLY Elon Musk posts, every slot, "until I say stop".** `ALL_STAR_UNTIL` default is
  2100-01-01 (no end date); every post opens on Elon's face. To end it: set the `ALL_STAR_UNTIL` variable (or edit
  `bot/config.py`), and to bring back the trending slot set `TREND_SLOTS=22`.
- **Trending slot** (off since 10-29): topics sorted by how many platforms they trend on (X, Reddit, Google,
  Wikipedia), then by search traffic; the AI posts the hottest one, whoever it is about. It averaged ~410 views vs
  2,600+ for Elon Reels.
- `SLOT_WINDOW_HOURS=3`: one post per slot window.
- The hourly GitHub cron (`:17`) is a backup in case cron-job.org misses.
- The workflow has `concurrency: trend-bot` and checks out `ref: main`, so a queued run sees the previous run's post.

## 5. End-to-end flow (`bot/main.py`)

1. `insights.update_stats`: reads views, reactions, comments and shares of recent posts.
   - Score = views/5 + 2×reactions + 3×comments + 3×min(shares,2). Shares are capped because the owner shares by hand.
2. `should_post_now`: if this is not a slot, run `engage()` (comment replies + daily to-do) and exit.
3. **Collect topics** (`trends.collect` + `rank`): Google Trends, Google News (US/GB/CA/AU), focus names, X trends
   (trends24, falling back to getdaytrends), Reddit top (RSS), and Wikipedia most-viewed people (living, not convicted).
   - Topics trending on several platforms get a "buzz" multiplier.
4. **Filter**: block words (death, war, crime...), already-posted topics, and stories that are recent duplicates.
5. **Star slot**: the star is `insights.star_face`, the best face by views, currently Elon Musk.
   - Pool = topics about the star or his companies, plus `trends.star_sources`:
     - Google News for Elon Musk, Tesla, SpaceX, Starlink, xAI Grok, Neuralink, Cybertruck and Optimus robot.
     - Reddit fan subs (often 429 on runners).
     - `trends.person_facts`: a different Wikipedia section each slot.
   - The prompt also gets `insights.star_angles` (the best and worst star posts), so the AI copies winning angles
     (money, power, clashes, shock moves) and avoids losing ones (stock-market and India angles).
6. **Real facts** (`main.add_facts` → `trends.article_facts`): the top 4 topics get Bing News reports from several
   outlets plus one full article (read with trafilatura). Google News RSS only gives headlines, so before this the AI
   wrote whole Reels from one line.
7. **AI writes posts** (`ai.write_posts`).
   - Order: Gemini → Groq → Cloudflare Llama 3.3. If all are busy, wait 65 s and try once more.
   - Each post: headline (PERSONALITY + MONEY + CLASH formula), caption, hashtags, wiki_title, a 30-36 word
     voiceover (HOOK max 8 words → STORY in concrete facts → TWIST → question; no vague filler), scenes `[{line, show: face|video, query, photo}]`, video_queries and first_comment.
8. **Checks per post**:
   - `star_only`: must be about the star.
   - Duplicate checks:
     - Word-overlap check, with a synonym map ("richest" = "trillionaire" = "net worth" → wealth theme, at most once
       per 72 h).
     - 60-day check for star stories.
     - **Same name within 24 h** (`main.same_name_today`, 10-29): two star posts may not share another name
       (Putin, Cybercab, Starlink...) within a day; Tesla/SpaceX/X don't count (`COMMON_NAMES`).
     - **AI same-story check** (`ai.same_story`, 7-day window per face since 10-29; also the same storyline seen
       from the other side, e.g. a reaction or follow-up).
   - **AI editor** (`ai.edit_reel`, all posts since 10-27, max 3 per slot): Reels are scored on hook, clarity, sense
     and facts (the lowest counts), photos on facts. "Facts" means headline, narration and caption say EXACTLY what
     the reports say ('offers buyouts' is not 'fires', 'plans' is not 'launches', claims stay claims). Under 8/10 it has the script rewritten once; still under 8 → try the next story; if no story reaches 8,
     post the best-scored one (never an empty slot).
   - Wording checks: `BLOCK_WORDS`, `RISKY` (sexual words, slurs, profanity) and `BAIT` (engagement bait removed
     from captions).
   - If every star idea is a repeat: ask the AI 2 more times, then (since 10-27) up to 3 FACTS posts from other
     sections of the star's Wikipedia article (`person_facts(shift=n)`; 5 in Elon-only mode). In Elon-only mode the
     last tries are up to 4 facts posts about one of the star's companies/products (`STAR_WORLD`, e.g. "Elon Musk's
     Tesla Model Y..."), with all duplicate checks; never another face; and the run no longer stops when every
     collected topic is old. Outside Elon-only mode: another huge face (`main.BIG_FACES`:
     Trump, Zuckerberg, Bezos, Gates, Altman, Huang, Taylor Swift, Ronaldo, Messi, LeBron, Kim K, The Rock, MrBeast,
     Obama, Rihanna, Oprah, Tom Cruise, Kylie Jenner). The slot is never left empty.
9. **Poster** (`poster.make_poster`).
   - The real photo of the person comes from Wikipedia, then Commons, then Openverse.
   - Wikipedia name lists such as "Dan Levy" are resolved with a search plus a role hint.
   - A story with no face photo is tried last.
10. **Reel** (`reel.make_reel`), 1080×1920, 30 fps.
   - **Voice**: Cloudflare Deepgram Aura (asteria) → Kokoro (local, free) → MeloTTS.
   - **Length**: 15 s normally, up to 17 s (`MAX_LEN`) so the whole point is said. Max speed-up 1.1×. The spoken
     follow line is dropped first, and a middle sentence only if the voice is over 17 s.
   - **Scenes follow the narration** (owner, 10-20: "the person's photo once at the start, then real scenes"):
     - The first scene is the person's face, and **only** the first.
     - Each line has a `wiki` (the Wikipedia title of the real thing, other person, place or event it names). The bot
       shows a real photo from that article's pictures (free licences only, ≥800 px, no charts/maps/logos, judged).
     - Else real stock video for the line (Pixabay → NASA → Commons), else a real Openverse photo, else other photos
       from the story's articles, then the star's world (`trends.STAR_WORLD`: Starship, Cybertruck, Optimus, Falcon 9,
       Starlink, Tesla...), a different article each time; else story footage. The face again only if nothing is found.
     - Since 10-27: a new picture every ~2 s (`MAX_SCENE=2.2`, about 8-10 scenes), never the same picture or clip
       twice, photo/video alternate when both fit, judge quality ≥7 for photos. Openverse is skipped for the rest of
       a run after one timeout.
     - Landscape photos are shown whole on a blurred copy instead of a thin cropped strip.
     - **No AI images.**
   - No headline text on top (owner's choice); word-by-word subtitles under the face (`SUB_Y=1225`, face at
     `FACE_Y=720`), in the safe zone (Facebook covers the bottom ~35%), a follow banner and music from `assets/music`.
11. **Publish** (`meta.py`).
    - Facebook Reel (`video_reels`) or photo.
    - **Instagram**: the same Reel via resumable upload from the file (`rupload.facebook.com`), and photos via
      `image_url`.
    - The bot then writes its first comment.
    - If the Reel upload fails, it posts the photo instead.
12. Save the record to `state/posted.json` (key, topic, headline, badge, slot, face, format and post IDs). The workflow
    commits the state with `pull --rebase -X theirs` and retries.
13. `engage()` **after** posting:
    - Replies to every comment on posts from the last 7 days, max 100 per run.
    - Replies use the small Groq model `llama-3.1-8b-instant`, so they never use up the quota the posts need.
    - Sends the daily growth to-do (a GitHub issue assigned to the owner; e-mail with share links and USA groups).

## 6. Files

| File | Role |
|---|---|
| `bot/main.py` | Orchestration, slots, star logic, duplicate checks, fallbacks, publishing, records |
| `bot/config.py` | Env settings, slots, `ALL_STAR_UNTIL`, `GRAPH_VERSION` (v23.0) |
| `bot/trends.py` | All topic sources: FOCUS/FACES lists, `star_sources`, `person_facts`, `article_facts` (Bing News + full article), `x_trends`, `reddit_top`, `wiki_people`, `rank` |
| `bot/ai.py` | Prompt, Gemini/Groq/Cloudflare writers, `_light`, `same_story`, `edit_reel` (AI editor), `judge_image` (Gemini → Groq vision → Cloudflare llama-4-scout), `BAIT` |
| `bot/poster.py` | Photo search (Wikipedia/Commons/Openverse), poster templates (default "card"), downloads with 429 retry |
| `bot/reel.py` | Reel rendering (PIL + imageio-ffmpeg), TTS, scene planning, stock video sources |
| `bot/meta.py` | Graph API: connection check (FB + IG), photo, Reel and Instagram publish |
| `bot/insights.py` | Stats, score, face stats, best slot/format, `star_angles`, comment replies, performance summary for the AI |
| `bot/growth.py` | Follow line, share links, USA group lists, daily to-do issue |
| `state/posted.json` | Memory: `posted` records, `replied` comment IDs, `todo_date` |
| `state/report.md` | Auto performance report (categories, formats, slots, faces, top posts) |
| `.github/workflows/autopost.yml` | Workflow: inputs dry_run, format (reel/photo) and voice (kokoro); 25 min timeout; uploads the output folder (without raw clips) for 14 days |
| `branding/` | profile.png, cover.png, app icon, privacy policy text (not committed) |

## 7. Results history

| Date | Posts | Views | Reactions | Comments | Notes |
|---|---|---|---|---|---|
| 10-07 | ~20 | small | | | Reels 50-164 views vs photos 0-50 → Reel-first |
| 10-11 | 39 | 3,341 | 52 | 4 | Elon medal Reel 1,219 views |
| 10-13 | 51 | 13,182 | 827 | 147 | Elon trillionaire Reel 2,617 |
| 10-14 | 54 | 27,492 | 2,113 | 270 | "Elon faces critics" Reel 10k+ views over time |
| 10-15 | 60 | 32,408 | 2,517 | 291 | All 6 Elon slots posted |
| 10-17 | 72 | 43,540 | 5,347 | 560 | FB page ~445 followers; 722 comments auto-replied |

**Learnings:**
- **Reels beat photos.** Average score is about 485 for Reels vs 43 for photos.
- **Elon Musk is the winning face.** He averages ~1,100+ views per post; everyone else gets 30-70.
- **The best slot is 21 UTC (5 PM ET)**, then 19 UTC.
- Winning angles: power, wealth, rivalry/clash, shock product moves (Roadster, Boring Company, critics showdown).
- Losing angles: stock-market moves, India/Starlink business.

## 8. Change log (main milestones)

- **10-04**: Live. Posters, Gemini/Groq, cron-job.org.
- **10-06**:
  - Reels with stock video and voiceover.
  - Insights, first comment and an image judge.
- **10-07**:
  - Reel safe-zone layout, B-roll judge and scenes that follow the narration.
  - Focus on famous US faces.
- **10-08**:
  - Sources: X, Reddit and Wikipedia people.
  - Views-led score; no white headline card.
  - 6 posts a day, slot-based formats.
- **10-09**: Star slot (best face), Reels open on the face, comment questions.
- **10-11**:
  - Openverse photos, NASA/Commons video, Kokoro voice backup, 48 h duplicate check.
  - Reels made exactly 15 s.
  - 2-day all-Elon test; reply to every comment.
- **10-13**:
  - Wikipedia disambiguation fix (Dan Levy).
  - Face-first story order; stricter scene quality; no cropped duplicates.
  - All-Elon period extended to Oct 20, photos included.
- **10-14**:
  - Never an empty or double slot (checkout main, retries, BIG_FACES fallback).
  - Wikipedia section rotation for facts; raw clips no longer saved (storage).
- **10-15**:
  - Facebook-safe wording; multi-source Elon data with best/worst angles.
  - Instagram connected (profile photo, name and bio set to match FB; new bio on both).
- **10-17**:
  - Comment replies after posting on a light model; Cloudflare writer and 65 s retry; AI same-story check.
  - Instagram Reels by resumable upload.
  - No AI images in Reels; closing question shows the face.
  - Reels 15-17 s.
- **10-19**: Photo slot moved from 10:05 PM ET (02 UTC) to 7:05 PM ET (23 UTC).
- **10-20**:
  - New 6:05 PM ET (22 UTC) trending Reel slot: the hottest topic across all platforms.
  - Better Reels (owner: "no hook, no sense, no content"): real article facts, hook → story → twist → question
    script, AI editor with rewrite, subtitles lower. Pushed without a GitHub dry run (owner's call): check the
    first runs' logs for `[facts]` and `[editor]` lines.
  - Bing reports filtered: they must share 60% of the search's main words (stopped unrelated stories mixing in).
  - Real scenes: face once at the start, then real Wikipedia/Commons photos of what each line names.
- **10-27**:
  - Star slots no longer fall back to other faces before 3 extra Elon facts posts (5 PM went to Oprah on 10-26).
  - More scenes (~2 s each), no repeated pictures, Elon-world professional photos as fallback, stricter quality.
  - Headline accuracy (prompt rule + editor checks headline/caption on every post) and risky legal topics
  blocked (`BLOCK_WORDS`: indictments, raids, subpoenas, FBI, federal/criminal probes, homes searched).
- **10-29**:
  - Only Elon Musk posts, every slot including 6 PM, no end date; never another face; the last resort is facts
    about Elon's companies/products, still with every duplicate check.
  - Stricter duplicates: same name within 24 h, AI same-story over 7 days incl. same storyline (Putin pair on 10-28).

## 9. Known gotchas (for whoever edits the code)

- The **prompt in `ai.build_prompt` is an f-string.** Literal JSON braces must be doubled `{{ }}`; a missed pair once
  crashed a run.
- **Shell heredocs turn `\n`, `\b` and `\x00` into real control characters.** Edit with a proper editor, and check
  for `chr(0)`/`chr(8)` in files after scripted edits.
- **Don't push code while a live run is in progress.** Push between slots.
- The **Gemini free quota runs out often (429)**, and the Groq big models have per-minute token limits. That is why
  replies use `llama-3.1-8b-instant` and Cloudflare is the third writer.
- Wikimedia serves only standard thumbnail sizes (use 1280) and rate-limits (429). Flickr needs a browser UA and
  Wikimedia a bot UA.
- Reddit and trends24 are often blocked or rate-limited on GitHub runners. The code skips them quietly.
- Facebook video nodes have no `reactions` field (use likes). Page comments are excluded from comment counts.
- Run logs can be read at `github.com/<repo>/commit/<FULL sha>/checks/<job id>/logs` (the short sha gives a 500).
- Local Windows runs need `PYTHONUTF8=1`. Tests can mock AI and network calls (see the scratch tests used before).

## 10. Pending / next steps

1. **After the next live Reel**: confirm that the Instagram Reel posted (resumable upload) and that the new scene rules
   look good.
2. Elon-only has no end date (owner, 10-29); the owner will say when to stop.
3. **2026-11-01 (DST ends)**:
   - The UTC slots will land 1 hour earlier in ET. Shift every slot by +1 h UTC to keep the same ET times:
     `POST_SLOTS=0,2,4,5,20,22,23`, `PHOTO_SLOTS=0,5`, `STAR_SLOTS=20,22` (and `TREND_SLOTS=23` if the trending slot comes back).
   - Update cron-job.org (Karachi): `5 1,3,4,5,7,9,10 * * *`.
4. The owner adds the Instagram website link (`facebook.com/hypeatlasnews`) in the IG app (mobile only).
5. Ideas not done:
   - Instagram comment replies (needs `instagram_manage_comments`).
   - Turn photo slots into Reels if photos stay weak.
   - Change the IG username to match FB (hypeatlasnews) if available.

## 11. How to test or operate

- **Dry run**: GitHub → Actions → Trend Auto Post → Run workflow → Dry run = true, format = reel/photo. The workflow
  checks out the branch it runs on, so new code can be dry-run from a test branch ("Use workflow from") before main. Dry runs
  between slots act as the next slot. They post nothing and don't use up topics.
- **Live single post**: Dry run = false (posts immediately; get the owner's OK first).
- **Check results**: `state/report.md` and `state/posted.json` (pull the repo), or the run logs.
