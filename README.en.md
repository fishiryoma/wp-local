# WordPress Static Site Build Guide

[繁體中文](README.md) | **English**

A static architecture for WordPress: content is exported through an automated Python pipeline and deployed to Cloudflare's edge network (Pages / R2 / Workers), replacing the original dynamic site.

## Architecture

| Service                               | Purpose                                                  |
| ------------------------------------- | -------------------------------------------------------- |
| Cloudflare Pages                      | Static HTML / CSS / JS                                   |
| Cloudflare R2 (`your-uploads-bucket`) | Images and media files                                   |
| Cloudflare Worker (`your-site-name`)  | Routing: image requests → R2, everything else → Pages    |
| Local WP (`your-site-local.local`)    | Local WordPress, used for writing content and building   |

All site-specific domains, bucket names, and project names are configured via `.env` (see `.env.example`).
Replace placeholders such as `your-site-local.local` and `your-domain.com` in the examples below with your own values.

---

## Publishing and Update Workflow

There are two scenarios: **does your change affect a single post, or the whole site?**

|                             | Quick build                                | Full build               |
| --------------------------- | ------------------------------------------ | ------------------------ |
| Command                     | `python new-post.py <post-url>`            | `python build-static.py` |
| Rebuild scope               | The post + homepage + its category/tag pages | Every page on the site |
| Image compression + R2 sync | ✅                                         | ✅                       |
| themes/plugins/wp-includes  | ❌ Not copied                              | ✅ Copied                |
| DB backup check             | ✅                                         | ✅                       |
| Automatic deploy            | ✅                                         | ✅                       |
| Time                        | ~1 minute                                  | Several to tens of minutes |

Both **deploy automatically**.

---

### Scenario 1: Quick build — editing a single post (most common)

**When to use:** Adding a new post, or editing the content/images of an existing post.

1. Open Local WP, then write or edit the post in the WordPress admin and click Update.

2. **Only when adding a new post**, refresh the URL list first by opening this in your browser:

    ```
    http://your-site-local.local/get-all-urls.php
    ```

    Wait until it shows `"status": "done"`. You can skip this step when editing an existing post.

3. Run:

    ```powershell
    cd "<your-project-root>"
    python new-post.py http://your-site-local.local/your-post-slug/
    ```

[new-post.py](new-post.py) **runs**:

1. **DB backup check** (`backup-db.py`) — only actually backs up if ≥ 30 days have passed since the last backup; otherwise it just compares dates and takes almost no time. A failed backup only prints a warning and does not abort publishing.
2. **Compress new images** (`compress-images.py`; see "Image Compression and R2 Upload" below)
3. **Sync images to Cloudflare R2** (`sync-images.py`; see "Image Compression and R2 Upload" below)
4. **Analytics sync** (`sync-analytics.py`) — called internally by `quick-publish.py` before building HTML, not a separate step
5. **Build HTML** (`quick-publish.py`) — rebuilds only **the post itself + the homepage (pages 1 and 2) + the category/tag archive pages the post belongs to**
6. **Deploy to Cloudflare Pages** (`deploy-pages.py`)

**Skips**:

- HTML for all other pages (pages not affected by this post are left untouched)
- Copying static files from themes / plugins / wp-includes

---

### Scenario 2: Full build — site-wide settings, themes, plugins, or first build

**When to use:**

- You changed a site-wide setting that **appears on every page** (site title, menus, widgets, footer, etc.), which Scenario 1's single-post rebuild doesn't cover
- You switched themes or plugins
- You're building the whole site for the first time

1. Open Local WP and change the settings in the WordPress admin.

2. **If you added or deleted any posts/pages in the meantime**, refresh the URL list first (skip if you only changed settings):

    ```
    http://your-site-local.local/get-all-urls.php
    ```

    Wait for `"status": "done"`. This queries the WordPress database and writes the URLs of all public pages to `wp-content/uploads/all-urls.json`.

3. Run:

    ```powershell
    cd "<your-project-root>"

    # Full rebuild (including themes/plugins/wp-includes)
    python build-static.py

    # Re-fetch HTML only, keeping existing CSS/JS/fonts (about 3x faster)
    python build-static.py --html-only

    # Build without deploying, if you want to inspect the output first
    python build-static.py --no-deploy
    ```

[build-static.py](build-static.py) **runs**:

1. **DB backup check** (same as Scenario 1; only backs up if ≥ 30 days)
2. **Analytics sync** (`sync-analytics.py`)
3. **Compress new images** (`compress-images.py`)
4. **Sync images to Cloudflare R2** (`sync-images.py`)
5. **Fetch the site-wide URL list** and clean `deploy/`
6. **Fetch HTML for all pages** and copy themes / plugins / wp-includes
7. **Deploy to Cloudflare Pages** (`deploy-pages.py`)

**Skips**: with `--html-only`, the static file copy in step 6 is skipped (HTML is re-fetched only).

**When to use `--html-only`:** You changed WordPress settings, but the theme/plugin files themselves haven't changed.

---

## Other Scripts

The two scenarios above cover all day-to-day needs. This section lists single-step scripts for advanced use or recovery.

### `deploy-pages.py` — Deploy only

```powershell
python deploy-pages.py
```

Pushes the existing `deploy/` folder to Cloudflare Pages (production). Use it when:

- You need to deploy after running `build-static.py --no-deploy`
- `build-static.py` skipped deploying because some pages failed to fetch, and you've checked and fixed `_failed_urls.txt`
- You ran `quick-publish.py` on its own and now need to deploy

### `quick-publish.py` — Rebuild a single post's HTML only

```powershell
python quick-publish.py http://your-site-local.local/your-post-slug/
python deploy-pages.py    # remember to deploy yourself
```

This is the build step of Scenario 1 and can also be run on its own. It **does not compress images, sync to R2, back up the DB, or deploy**.

> ⚠️ Only use this when you're **certain no images were changed** and want to save the image comparison time. If you guess wrong (you did change images but used this script), the images will never be uploaded to R2 and the live site will show broken images. Also, relying on this script long-term means the 30-day DB backup never runs. **For daily use, always use Scenario 1.**

### `cloudflare-worker/` — Cloudflare Worker code

Not part of the publishing workflow; only needed when you change the routing logic itself. For deployment settings, copy `wrangler.toml.example` to
`wrangler.toml` and fill in your own values.

| File                    | Description                                       |
| ----------------------- | ------------------------------------------------- |
| `worker.js`             | Worker logic: images go to R2, everything else to Pages |
| `wrangler.toml.example` | Worker config template (name, R2 binding)         |

**Updating the Worker:**

```powershell
cd "<your-project-root>\cloudflare-worker"
npx wrangler deploy
```

---

### Analytics Sync (Popular Posts Ranking)

After going static, the Cocoon theme can no longer track page views. These components pull data back from Cloudflare Analytics so the Popular Posts (人気記事) widget reflects real traffic. There are two parts:

**1. `analytics-worker/` — Fetches data automatically every day (no manual action needed)**

A separately deployed Cloudflare Worker that uses a cron trigger to fetch "yesterday's" CDN page traffic at a fixed time each day, accumulating it in Workers KV (keeping the last 90 days). This is the primary data source. You normally don't need to touch it — only when deploying for the first time or changing the worker logic (for deployment settings, copy `wrangler.toml.example` to `wrangler.toml` and fill in your own Account ID / KV Namespace ID):

```powershell
cd "<your-project-root>\analytics-worker"
npx wrangler deploy
```

**2. `sync-analytics.py` — Writes KV data into WordPress**

**Runs automatically on every build** (both `quick-publish.py` and `build-static.py` call its default mode), so you normally don't need to run it manually:

```powershell
cd "<your-project-root>"

# Default: read not-yet-applied data from KV and write it to wp_cocoon_accesses (the mode called during builds)
python sync-analytics.py

# Backfill: when KV has gaps (e.g. the window before analytics-worker was deployed), manually
# fetch the last 8 days from the Cloudflare GraphQL API into KV. This is not a full history
# tool — it can only fetch the last 8 days; dates beyond Cloudflare's API retention are skipped automatically
python sync-analytics.py --backfill
```

> Sync state is stored in `.analytics-sync.json` (already in .gitignore), recording the last date applied.

---

### Image Compression and R2 Upload — Running Separately

Use these when you only want to re-compress/re-upload images, e.g. batch re-compressing all existing images.

**Step 1: Compress images**

```powershell
cd "<your-project-root>"

# Process only new/modified images (normal use)
python compress-images.py

# Reprocess all images (first run, or when everything needs re-compressing)
python compress-images.py --all
```

> JPEG/WebP use quality 85; PNG uses lossless compression. Files smaller than 50KB are skipped automatically.
> Incremental detection compares size + mtime first; if neither changed, the file isn't read at all. Only changed files get an MD5 hash to confirm.
> (MD5 is a hash algorithm that turns file contents into a fixed-length string; any difference in content produces a different result, so it precisely detects whether two files are identical regardless of filename or timestamp.)

**Step 2: Sync to R2**

```powershell
cd "<your-project-root>"

# Normal sync (add/update only; normal use)
python sync-images.py

# List differences only, without uploading (to check what would be uploaded)
python sync-images.py --dry-run

# Full sync (files deleted locally are also removed from R2)
python sync-images.py --delete
```

> Comparison method: lists all R2 objects and all local files, compares keys and file sizes, and uploads only new files or files whose size differs.
> Without `--delete` (the default), it only adds/updates files and never deletes anything from R2.
