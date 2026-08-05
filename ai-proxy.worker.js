// Draft War Room — optional keyless AI proxy (Cloudflare Worker).
//
// Deploy this once and your leaguemates get the AI copilot without anyone pasting an API key: the
// real key lives here as a server secret, never in a browser. Pick the "Shared link" provider in
// League Setup and paste this Worker's URL.
//
// ── Deploy (free, ~3 minutes) ────────────────────────────────────────────────────────────────
//  1. Get a free Gemini key: https://aistudio.google.com/apikey  (no credit card)
//  2. Go to https://dash.cloudflare.com  →  Workers & Pages  →  Create  →  Create Worker.
//  3. Replace the generated code with this file's contents and Deploy.
//  4. In the Worker's Settings → Variables, add a SECRET named GEMINI_KEY = your key from step 1.
//  5. (Recommended) In ALLOW_ORIGINS below, replace "*" with your site, e.g.
//     "https://bucsfan1197.github.io", so only your app can use the Worker.
//  6. Copy the Worker URL (…workers.dev) and paste it into League Setup → AI → Shared link.
//
// The client sends { system, context, question }; this returns { text } (or { error }).

const ALLOW_ORIGINS = ["*"]; // replace with ["https://YOURNAME.github.io"] to lock it down

function cors(origin) {
  const ok = ALLOW_ORIGINS.includes("*") || ALLOW_ORIGINS.includes(origin);
  return {
    "Access-Control-Allow-Origin": ok ? (origin || "*") : ALLOW_ORIGINS[0],
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "content-type": "application/json",
  };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const headers = cors(origin);
    if (request.method === "OPTIONS") return new Response(null, { headers });
    if (request.method !== "POST")
      return new Response(JSON.stringify({ error: "POST only" }), { status: 405, headers });
    if (!env.GEMINI_KEY)
      return new Response(JSON.stringify({ error: "Worker missing GEMINI_KEY secret" }), { status: 500, headers });

    let body;
    try { body = await request.json(); }
    catch { return new Response(JSON.stringify({ error: "Bad JSON" }), { status: 400, headers }); }

    const system = String(body.system || "");
    const context = String(body.context || "");
    const question = String(body.question || "");
    const user = `CONTEXT (the app's computed numbers — reason only from this):\n${context}\n\nQUESTION: ${question}`;

    const payload = {
      system_instruction: { parts: [{ text: system }] },
      contents: [{ role: "user", parts: [{ text: user }] }],
      generationConfig: { temperature: 0.4, maxOutputTokens: 900 },
    };

    // Try a couple of cheap, widely-available flash models in order.
    const models = ["gemini-2.0-flash", "gemini-flash-latest", "gemini-1.5-flash"];
    let lastErr = "No usable model";
    for (const m of models) {
      let r, d;
      try {
        r = await fetch(
          `https://generativelanguage.googleapis.com/v1beta/models/${m}:generateContent?key=${encodeURIComponent(env.GEMINI_KEY)}`,
          { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) }
        );
        d = await r.json();
      } catch (e) { lastErr = String(e); continue; }
      if (r.ok) {
        const t = d?.candidates?.[0]?.content?.parts?.[0]?.text;
        if (t) return new Response(JSON.stringify({ text: t }), { headers });
        lastErr = "Empty answer from " + m;
        continue;
      }
      lastErr = d?.error?.message || ("HTTP " + r.status);
      if (!/quota|limit|not.?found|not.?supported|unavailable|404|429/i.test(lastErr)) break;
    }
    return new Response(JSON.stringify({ error: lastErr }), { status: 502, headers });
  },
};
