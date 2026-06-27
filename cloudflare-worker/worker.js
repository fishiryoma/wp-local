const PAGES_URL = "https://tesstaiwan.pages.dev";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Images from R2
    if (url.pathname.startsWith('/wp-content/uploads/')) {
      const key = decodeURIComponent(url.pathname.slice('/wp-content/uploads/'.length)); // R2 keys have no wp-content/uploads/ prefix
      const object = await env.UPLOADS.get(key);

      if (!object) {
        return new Response('Not Found', { status: 404 });
      }

      const headers = new Headers();
      object.writeHttpMetadata(headers);
      headers.set('cache-control', 'public, max-age=31536000, immutable');
      return new Response(object.body, { headers });
    }

    // Everything else from Pages
    const pagesRequest = new Request(
      PAGES_URL + url.pathname + url.search,
      request
    );
    return fetch(pagesRequest);
  }
}
