const FILES = new Map([
  ["instrumental.mp3", "audio/mpeg"],
  ["bass.wav", "audio/wav"],
  ["drums.wav", "audio/wav"],
  ["other.wav", "audio/wav"],
]);

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Range, If-None-Match, If-Modified-Since",
    "Access-Control-Expose-Headers": "Accept-Ranges, Content-Length, Content-Range, ETag",
    "Vary": "Origin",
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders() });
    if (!["GET", "HEAD"].includes(request.method)) {
      return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, HEAD, OPTIONS", ...corsHeaders() } });
    }

    const match = url.pathname.match(/^\/v1\/([A-Za-z0-9_-]{10,64})\/(instrumental\.mp3|bass\.wav|drums\.wav|other\.wav)$/);
    if (!match) return new Response("Not Found", { status: 404, headers: corsHeaders() });
    const [, trackId, file] = match;
    const key = `songs/${trackId}/${file}`;
    const object = request.method === "HEAD"
      ? await env.AUDIO.head(key)
      : await env.AUDIO.get(key, { onlyIf: request.headers, range: request.headers });
    if (!object) return new Response("Not Found", { status: 404, headers: corsHeaders() });

    const headers = new Headers(corsHeaders());
    object.writeHttpMetadata(headers);
    headers.set("ETag", object.httpEtag);
    headers.set("Accept-Ranges", "bytes");
    headers.set("Cache-Control", "public, max-age=86400, stale-while-revalidate=604800");
    headers.set("Content-Type", FILES.get(file));
    if ("range" in object && object.range) {
      const offset = object.range.offset ?? Math.max(0, object.size - (object.range.suffix || object.size));
      const length = object.range.length ?? Math.min(object.range.suffix || object.size, object.size - offset);
      headers.set("Content-Range", `bytes ${offset}-${offset + length - 1}/${object.size}`);
      headers.set("Content-Length", String(length));
    } else {
      headers.set("Content-Length", String(object.size));
    }
    if (request.method === "HEAD") return new Response(null, { status: 200, headers });
    if (!("body" in object)) {
      const status = request.headers.has("If-None-Match") || request.headers.has("If-Modified-Since") ? 304 : 412;
      return new Response(null, { status, headers });
    }
    return new Response(object.body, { status: object.range ? 206 : 200, headers });
  },
};
