# Trend Auto-Post Bot (Facebook + Instagram)

Runs free on GitHub Actions, even when your PC is off. Every 4 hours, it:

1. Checks what's trending in the US and UK (Google Trends and Google News)
2. Filters out sensitive topics, such as deaths, crime and war
3. Has Gemini AI (free) write a headline, a caption and hashtags
4. Draws a 1080x1350 poster
5. Posts it to your Facebook Page and Instagram through the official Meta API

Cost: **Rs 0**, using free tiers of Gemini, GitHub Actions and the Meta Graph API.

---

## Step 1: Link Facebook Page and Instagram
1. Switch your Instagram account to a **Professional** account: Settings → Account type → Business or Creator.
2. On your Facebook Page, go to Settings → Linked accounts → Instagram → Connect.

## Step 2: Get a Gemini API key (free)
1. Open https://aistudio.google.com and sign in with Google.
2. Click **Get API key → Create API key**, then copy it. This is your `GEMINI_API_KEY`.

## Step 3: Create a Meta Developer app and get tokens
1. Open https://developers.facebook.com, log in, and click **Get Started**.
2. Go to **My Apps → Create App**, name it (for example "Trend Bot"), and choose the use cases or type that let you **manage Pages** and **publish Instagram content**. If asked, pick "Other" and then "Business".
3. Open **Tools → Graph API Explorer** (https://developers.facebook.com/tools/explorer).
   - Meta App: select your app.
   - User or Page: choose **User Token**.
   - Add these permissions: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`, `business_management`.
   - Click **Generate Access Token**. In the popup, select your Page and your Instagram account, then allow.
4. Copy the token and open **Access Token Debugger** (https://developers.facebook.com/tools/debug/accesstoken). Paste it, click **Debug**, then **Extend Access Token** at the bottom. Copy the new long-lived token.
5. Go back to Graph API Explorer, paste the long-lived token in the Access Token box, and run:
   `me/accounts`
   The result shows your Page's `id` (this is **FB_PAGE_ID**) and its `access_token` (this is **FB_PAGE_TOKEN**). A page token taken from a long-lived user token does not expire. You can confirm "Expires: Never" in the debugger.
6. Run: `YOUR_PAGE_ID?fields=instagram_business_account`
   The `id` inside is **IG_USER_ID**.

> Keep these tokens secret. Never post them anywhere or put them inside the code.

## Step 4 (strongly recommended): Pexels key for photo posters, free
Get a free key at https://www.pexels.com/api/. With it, every poster gets a real HD photo, and the templates rotate between "viral", "breaking" and "frame".
Without it, the bot falls back to a text-only graphic design.

Optional: for AI-generated background art, get a key at https://enter.pollinations.ai and add it as the secret `POLLINATIONS_API_KEY`. This service is pay-as-you-go.

## Step 5: Put the bot on GitHub
1. Create an account at https://github.com.
2. Click **+ → New repository**, name it `trend-bot`, choose **Private**, and click Create.
3. Click **uploading an existing file**. Drag in **all** the files and folders from this zip, including the `.github` folder, then click **Commit changes**.
   - If the `.github` folder doesn't upload, go to **Add file → Create new file**, type the name `.github/workflows/autopost.yml`, paste in the contents of that file, and commit.

## Step 6: Add secrets and settings
Go to Repo **Settings → Secrets and variables → Actions**.

**Secrets** tab, then **New repository secret**, one at a time:

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | from Step 2 |
| `FB_PAGE_ID` | from Step 3 |
| `FB_PAGE_TOKEN` | from Step 3 |
| `IG_USER_ID` | from Step 3 |
| `PEXELS_API_KEY` | from Step 4 (recommended) |
| `POLLINATIONS_API_KEY` | optional, for AI art |
| `GROQ_API_KEY` | optional backup AI (console.groq.com) |

**Variables** tab, then **New repository variable**:

| Name | Example value |
|---|---|
| `PAGE_NAME` | `Trend Buzz USA` |
| `NICHE` | `entertainment, celebrities, sports and tech` |
| `DRY_RUN` | `true` (test mode, nothing is uploaded) |
| `COUNTRIES` | `US,GB` (optional) |
| `POSTS_PER_RUN` | `1` (optional) |

## Step 7: Test run
1. Open the repo's **Actions** tab and enable workflows if asked.
2. Click **Trend Auto Post → Run workflow**.
3. When it finishes (green tick), open the run. Under **Artifacts**, download `posters` to see the image and caption.
4. If you're happy with it, change the `DRY_RUN` variable to `false`. The bot is now live and posts every 4 hours automatically.

## Changing the schedule
Edit `.github/workflows/autopost.yml`, line `cron: "17 */4 * * *"`:
- `"17 */3 * * *"` = every 3 hours
- `"17 */6 * * *"` = every 6 hours
- `"17 13,17,21 * * *"` = 3 fixed times a day. These are in UTC, and Pakistan time = UTC+5.

GitHub can delay scheduled runs by a few minutes. That's normal.

## Troubleshooting
- **Gemini 404 / model error:** Add a variable `GEMINI_MODEL` = `gemini-2.5-flash`, or whichever model aistudio.google.com currently lists.
- **Meta "(#200) permission" error:** The token is missing a permission. Redo Step 3 and tick all the permissions.
- **Meta "Invalid OAuth access token":** The token expired or was copied wrong. Redo Step 3, parts 4–5.
- **Meta version error:** Add a variable `GRAPH_VERSION` with the latest version shown in Graph API Explorer, for example `v24.0`.
- **Instagram posts but Facebook fails, or the other way round:** Each platform runs independently. Check the run log for the exact error.

Fonts: Poppins (SIL Open Font License).
