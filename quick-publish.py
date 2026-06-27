"""
quick-publish.py
Rebuilds only the pages affected by a new/updated post.

Usage:
    python quick-publish.py https://tesstaiwan-local.local/your-post-slug/

What it rebuilds:
    - The post itself
    - Homepage (page 1 and 2)
    - All category archive pages the post belongs to
    - All tag archive pages the post belongs to

After running, deploy with:
    npx wrangler pages deploy deploy --project-name tesstaiwan
"""

import sys
import time
import requests
from pathlib import Path
from urllib.parse import urlparse

# ── Config ──────────────────────────────────────────────────────
LOCAL_URL  = "http://tesstaiwan-local.local"
PROD_URL   = "https://tesstaiwan.com"
DEPLOY_DIR = Path(r"C:\Users\User\Local Sites\tesstaiwan-local\deploy")
TIMEOUT    = 60
# ────────────────────────────────────────────────────────────────

session = requests.Session()


def to_local(url):
    return url.replace(PROD_URL, LOCAL_URL)


def url_to_filepath(url):
    path = urlparse(url).path
    if not path or path == "/":
        return DEPLOY_DIR / "index.html"
    path = path.strip("/")
    if "." not in Path(path).name:
        return DEPLOY_DIR / path / "index.html"
    return DEPLOY_DIR / path


def fetch_and_save(url):
    local = to_local(url)
    try:
        r = session.get(local, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"  SKIP {urlparse(local).path}  (HTTP {r.status_code})")
            return False
        html = r.text.replace(LOCAL_URL, PROD_URL)

        out = url_to_filepath(local)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8", errors="replace")
        print(f"  OK  {urlparse(local).path}")
        return True
    except Exception as e:
        print(f"  FAIL {urlparse(local).path}  ({e})")
        return False


def get_related_urls(post_url):
    """Use WP REST API to find categories and tags for this post."""
    urls = set()

    # Convert to local and get the slug
    local = to_local(post_url).rstrip("/")
    slug = local.split("/")[-1]

    # Try to find the post via REST API
    for post_type in ["posts", "pages"]:
        try:
            api = f"{LOCAL_URL}/wp-json/wp/v2/{post_type}?slug={slug}&_fields=id,categories,tags"
            r = session.get(api, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            data = r.json()
            if not data:
                continue

            post = data[0]

            # Category archive pages
            for cat_id in post.get("categories", []):
                cat_r = session.get(
                    f"{LOCAL_URL}/wp-json/wp/v2/categories/{cat_id}?_fields=link",
                    timeout=TIMEOUT
                )
                if cat_r.status_code == 200:
                    link = cat_r.json().get("link", "")
                    if link:
                        urls.add(link)

            # Tag archive pages
            for tag_id in post.get("tags", []):
                tag_r = session.get(
                    f"{LOCAL_URL}/wp-json/wp/v2/tags/{tag_id}?_fields=link",
                    timeout=TIMEOUT
                )
                if tag_r.status_code == 200:
                    link = tag_r.json().get("link", "")
                    if link:
                        urls.add(link)
            break
        except Exception:
            continue

    return urls


def main():
    if len(sys.argv) < 2:
        print("Usage: python quick-publish.py <post-url>")
        print("Example: python quick-publish.py https://tesstaiwan-local.local/my-post/")
        sys.exit(1)

    post_url = sys.argv[1].rstrip("/") + "/"

    start_time = time.time()

    print("=" * 50)
    print("  Quick Publish")
    print(f"  Post: {post_url}")
    print("=" * 50)

    urls = set()

    # The post itself
    urls.add(post_url)

    # Homepage (page 1 and 2)
    urls.add(LOCAL_URL + "/")
    urls.add(LOCAL_URL + "/page/2/")

    # Related category and tag pages
    print("\nFetching related archive pages...")
    related = get_related_urls(post_url)
    urls.update(related)
    if related:
        for u in related:
            print(f"  Found: {urlparse(to_local(u)).path}")

    print(f"\nRebuilding {len(urls)} pages...")
    ok = sum(fetch_and_save(u) for u in sorted(urls))

    elapsed = time.time() - start_time
    mins, secs = divmod(int(elapsed), 60)

    print(f"\nDone: {ok}/{len(urls)} pages updated.  Time: {mins}m {secs}s")
    print("\nNext step:")
    print('  npx wrangler pages deploy deploy --project-name tesstaiwan')


if __name__ == "__main__":
    main()
