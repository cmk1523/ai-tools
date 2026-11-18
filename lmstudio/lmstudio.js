const { McpServer } = require("@modelcontextprotocol/sdk/server/mcp.js");
const { spawn } = require("child_process");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const { z } = require("zod");
const fs = require("fs");
const path = require("path");
const { Parser } = require("@json2csv/plainjs");
const { flatten } = require("@json2csv/transforms");

// stdout is reserved for MCP protocol messages, so logs go to a file and stderr only
const LOG_FILE = process.env.LMSTUDIO_LOG_FILE || path.join(__dirname, "lmstudio.log");

function log(...args) {
    const line = `[${new Date().toISOString()}] ${args
        .map((a) => (typeof a === "string" ? a : JSON.stringify(a)))
        .join(" ")}\n`;
    try {
        fs.appendFileSync(LOG_FILE, line);
    } catch {}
    console.error(line.trimEnd());
}

// MCP Server configuration
const server = new McpServer({
    name: "lmstudio",
    version: "1.0.0",
});

/**
 * Helper function to run a shell command
 */
function runCommand(command) {
    return new Promise((resolve, reject) => {
        const process = spawn("/bin/zsh", ["-c", command], {
            stdio: ["pipe", "pipe", "pipe"],
        });

        let stdout = "";
        let stderr = "";

        process.stdout.on("data", (data) => {
            stdout += data.toString();
        });

        process.stderr.on("data", (data) => {
            stderr += data.toString();
        });

        process.on("close", (code) => {
            if (code === 0) {
                resolve({ stdout, stderr });
            } else {
                reject(new Error(`Command failed with code ${code}: ${stderr}`));
            }
        });

        process.on("error", (err) => {
            reject(err);
        });
    });
}

/**
 * Helpers for searching DuckDuckGo Lite and parsing its HTML results
 */
const DDG_LITE_URL = "https://lite.duckduckgo.com/lite/";

function decodeHtml(text) {
    const codePoint = (n) => {
        try {
            return String.fromCodePoint(n);
        } catch {
            return "";
        }
    };
    return text
        .replace(/<[^>]*>/g, "")
        .replace(/&#x([0-9a-f]+);/gi, (_, h) => codePoint(parseInt(h, 16)))
        .replace(/&#(\d+);/g, (_, d) => codePoint(parseInt(d, 10)))
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&apos;/g, "'")
        .replace(/&nbsp;/g, " ")
        .replace(/&amp;/g, "&")
        .replace(/\s+/g, " ")
        .trim();
}

const BROWSER_UA =
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36";
const FETCH_TIMEOUT_MS = 10000;

/**
 * fetch() with a timeout and a readable error (Node's "fetch failed" hides the real cause).
 */
async function fetchWithTimeout(url, init = {}) {
    try {
        return await fetch(url, { ...init, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
    } catch (error) {
        const cause = error.cause && (error.cause.code || error.cause.message);
        if (error.name === "TimeoutError") throw new Error(`request timed out after ${FETCH_TIMEOUT_MS}ms`);
        throw new Error(cause ? `${error.message} (${cause})` : error.message);
    }
}

function browserHeaders(extra = {}) {
    return { "User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9", ...extra };
}

function resolveResultUrl(href) {
    const url = new URL(decodeHtml(href), DDG_LITE_URL);
    return url.searchParams.get("uddg") || url.href;
}

// Headers copied from a real Chrome session submitting the DDG Lite form (these get past its bot check).
const DDG_LITE_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    Origin: "https://lite.duckduckgo.com",
    Referer: "https://lite.duckduckgo.com/",
    "Sec-Ch-Ua": '"Not-A.Brand";v="99", "Google Chrome";v="157", "Chromium";v="157"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Upgrade-Insecure-Requests": "1",
    "User-Agent":
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
};

async function searchDuckDuckGoLite(query) {
    const response = await fetchWithTimeout(DDG_LITE_URL, {
        method: "POST",
        headers: browserHeaders(DDG_LITE_HEADERS),
        body: new URLSearchParams({ q: query }).toString(),
    });
    const html = await response.text();
    log(`[web_search q=${JSON.stringify(query)}] DDG Lite HTTP ${response.status}, ${html.length} bytes`);

    if (html.includes("anomaly-modal")) {
        throw new Error("DuckDuckGo blocked the request with a CAPTCHA; try again later");
    }
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }

    const links = [...html.matchAll(/<a[^>]*href="([^"]*)"[^>]*class=['"]result-link['"][^>]*>([\s\S]*?)<\/a>/g)];
    const snippets = [...html.matchAll(/<td[^>]*class=['"]result-snippet['"][^>]*>([\s\S]*?)<\/td>/g)];

    return links
        .map((link, i) => ({
            title: decodeHtml(link[2]),
            url: resolveResultUrl(link[1]),
            description: snippets[i] ? decodeHtml(snippets[i][1]) : "",
        }))
        .filter((r) => !r.url.includes("duckduckgo.com/y.js"));
}

/**
 * Brave Search (HTML, no key). Result blocks are <div class="snippet ..." data-type="web">.
 * Match on stable class names only; the svelte-* hash classes change between deploys.
 */
async function searchBrave(query) {
    const url = `https://search.brave.com/search?${new URLSearchParams({ q: query, source: "web" })}`;
    const response = await fetchWithTimeout(url, { headers: browserHeaders() });
    const html = await response.text();
    log(`[web_search q=${JSON.stringify(query)}] Brave HTTP ${response.status}, ${html.length} bytes`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const results = [];
    for (const block of html.split(/<div class="snippet[ "][^>]*data-type="web"/).slice(1)) {
        const link = block.match(/<a[^>]*href="(https?:\/\/[^"]+)"/);
        if (!link) continue;
        const title = block.match(/<div class="title[^"]*"[^>]*>([\s\S]*?)<\/div>/);
        const snippet = block.match(/<div class="content[^"]*"[^>]*>([\s\S]*?)<\/div>/);
        const resultUrl = decodeHtml(link[1]);
        if (/^https?:\/\/search\.brave\.com\//.test(resultUrl)) continue;
        results.push({
            title: title ? decodeHtml(title[1]) : resultUrl,
            url: resultUrl,
            description: snippet ? decodeHtml(snippet[1]) : "",
        });
    }
    return results;
}

/**
 * Bing result links are click-tracking redirects; the real URL is base64url in the `u` param
 * behind an "a1" prefix.
 */
function resolveBingUrl(href) {
    const url = new URL(decodeHtml(href), "https://www.bing.com/");
    const encoded = url.searchParams.get("u");
    if (url.hostname.endsWith("bing.com") && encoded && encoded.startsWith("a1")) {
        try {
            const decoded = Buffer.from(encoded.slice(2), "base64url").toString("utf8");
            if (/^https?:\/\//.test(decoded)) return decoded;
        } catch {}
    }
    return url.href;
}

async function searchBing(query) {
    const url = `https://www.bing.com/search?${new URLSearchParams({ q: query, setlang: "en-US", cc: "US" })}`;
    const response = await fetchWithTimeout(url, { headers: browserHeaders() });
    const html = await response.text();
    log(`[web_search q=${JSON.stringify(query)}] Bing HTTP ${response.status}, ${html.length} bytes`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const results = [];
    for (const block of html.split(/<li class="b_algo"/).slice(1)) {
        const link = block.match(/<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/);
        if (!link) continue;
        const snippet =
            block.match(/<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>([\s\S]*?)<\/p>/) ||
            block.match(/<div class="b_caption[^"]*"[^>]*>[\s\S]*?<p[^>]*>([\s\S]*?)<\/p>/);
        results.push({
            title: decodeHtml(link[2]),
            url: resolveBingUrl(link[1]),
            description: snippet ? decodeHtml(snippet[1]) : "",
        });
    }
    return results;
}

/**
 * DuckDuckGo Instant Answer API (JSON, no key). Not a full web search: only returns an
 * encyclopedia-style abstract plus related topics, so it is the last resort.
 */
async function searchDuckDuckGoInstant(query) {
    const url = `https://api.duckduckgo.com/?${new URLSearchParams({ q: query, format: "json", no_html: "1", skip_disambig: "1" })}`;
    const response = await fetchWithTimeout(url, { headers: browserHeaders() });
    log(`[web_search q=${JSON.stringify(query)}] DDG Instant HTTP ${response.status}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();

    const results = [];
    if (data.AbstractText && data.AbstractURL) {
        results.push({ title: data.Heading || query, url: data.AbstractURL, description: data.AbstractText });
    }
    for (const r of data.Results || []) {
        if (r.FirstURL) results.push({ title: decodeHtml(r.Text || r.FirstURL), url: r.FirstURL, description: "" });
    }
    const related = (data.RelatedTopics || []).flatMap((t) => t.Topics || [t]);
    for (const r of related) {
        if (!r.FirstURL || !r.Text || r.FirstURL.includes("duckduckgo.com/c/")) continue;
        const [head, ...rest] = r.Text.split(" - ");
        results.push({ title: head, url: r.FirstURL, description: rest.join(" - ") || r.Text });
    }
    return results;
}

const SEARCH_SOURCES = [
    { name: "DuckDuckGo Lite", search: searchDuckDuckGoLite },
    { name: "Brave", search: searchBrave },
    { name: "Bing", search: searchBing },
    { name: "DuckDuckGo Instant", search: searchDuckDuckGoInstant },
];

// Sources that rate-limit us (HTTP 429) or show a CAPTCHA are skipped for a while instead of re-hit.
const COOLDOWN_MS = 60000;
const cooldownUntil = new Map(); // search function -> timestamp

/**
 * Try each keyless source in order; the first one that returns results wins.
 * Throws only if every source errored; returns an empty list if some answered with nothing.
 */
async function webSearch(query, sources = SEARCH_SOURCES) {
    const failures = [];
    const skipped = []; // every earlier source that did not produce results, with the reason
    let answeredEmpty = false;
    for (const { name, search } of sources) {
        const started = Date.now();
        if ((cooldownUntil.get(search) || 0) > started) {
            failures.push(`${name}: skipped (rate-limited recently)`);
            skipped.push(`${name} (skipped, rate-limited recently)`);
            continue;
        }
        try {
            const results = await search(query);
            if (results.length > 0) {
                log(`[web_search q=${JSON.stringify(query)}] ${name} returned ${results.length} results in ${Date.now() - started}ms`);
                return { source: name, results, skipped };
            }
            answeredEmpty = true;
            skipped.push(`${name} (no results)`);
            log(`[web_search q=${JSON.stringify(query)}] ${name} returned 0 results, trying next source`);
        } catch (error) {
            failures.push(`${name}: ${error.message}`);
            skipped.push(`${name} (${error.message})`);
            if (/HTTP 429|CAPTCHA/.test(error.message)) cooldownUntil.set(search, Date.now() + COOLDOWN_MS);
            log(`[web_search q=${JSON.stringify(query)}] ${name} failed: ${error.message}, trying next source`);
        }
    }
    if (!answeredEmpty) throw new Error(`All search sources failed. ${failures.join("; ")}`);
    return { source: null, results: [] };
}

// === TOOL 1: Get Current Date ===
server.registerTool(
    "get_current_date",
    {
        title: "Get Current Date",
        description:
            "Get the current date and time from the system.\n\n" +
            "Use this tool when:\n" +
            "- The user asks 'What time is it?'\n" +
            "- The user asks for the current date\n" +
            "- You need to create a timestamp\n" +
            "- You're logging events and need current time\n" +
            "- The user asks about scheduling\n\n" +
            "Returns the full date and time in the system's default format.",
    },
    async () => {
        try {
            const result = await runCommand("date");
            return {
                content: [{
                    type: "text",
                    text: `Current date/time: ${result.stdout.trim()}`
                }]
            };
        } catch (error) {
            return {
                content: [{
                    type: "text",
                    text: `Error getting date: ${error.message}`
                }],
                isError: true
            };
        }
    }
);

// === TOOL 2: Web Search ===
server.registerTool(
    "web_search",
    {
        title: "Web Search",
        description:
            "Search the web (DuckDuckGo Lite, falling back to Brave, Bing, and the DuckDuckGo Instant Answer API; no API key needed) and return the top results, each with a title, URL, and short snippet.\n\n" +
            "Use this tool when:\n" +
            "- The user asks about recent events, news, or anything that may have changed after your training data\n" +
            "- The user asks for current facts such as prices, releases, versions, schedules, or weather\n" +
            "- The user explicitly asks you to search, look something up, or find a link or source\n" +
            "- You are unsure of a specific fact (a name, date, statistic, or quote) and need to verify it\n" +
            "- The user asks about a niche topic, product, person, or organization you know little about\n\n" +
            "Do NOT use this tool when:\n" +
            "- You can answer confidently from general knowledge (math, definitions, well-established facts)\n" +
            "- The user asks for creative writing, opinions, or help with code they have provided\n" +
            "- The question is about the current date or time (use get_current_date instead)\n\n" +
            "Tips for good queries:\n" +
            "- Write short keyword queries (3-8 words), not full sentences\n" +
            "- Include specific names, versions, or years to narrow results\n" +
            "- If results are poor, rephrase the query and search again rather than guessing\n\n" +
            "Results are snippets only, not full page content. Cite the URLs you rely on in your answer, " +
            "and tell the user if the results did not contain the answer.",
        inputSchema: {
            query: z.string().describe("Short keyword search query, e.g. 'node.js 24 release date'"),
            max_results: z
                .number()
                .int()
                .min(1)
                .max(10)
                .optional()
                .describe("Number of results to return (1-10, default 5)"),
        },
    },
    async ({ query, max_results = 5 }) => {
        const tag = `[web_search q=${JSON.stringify(query)}]`;
        const started = Date.now();
        log(`${tag} called, max_results=${max_results}`);
        try {
            const { source, results, skipped } = await webSearch(query);
            const top = results.slice(0, max_results);
            log(`${tag} ok via ${source}: ${results.length} parsed, returning ${top.length} in ${Date.now() - started}ms`);
            top.forEach((r, i) => log(`${tag}   ${i + 1}. ${r.title} — ${r.url}`));
            const text = top
                .map((r, i) => `${i + 1}. ${r.title}\n${r.url}\n${r.description}`)
                .join("\n\n");
            return {
                content: [{
                    type: "text",
                    text: text
                        ? `Search provider: ${source}\n` +
                          (skipped.length > 0 ? `Skipped providers: ${skipped.join("; ")}\n` : "") +
                          `\n${text}`
                        : `No results found for "${query}".`
                }]
            };
        } catch (error) {
            log(`${tag} error after ${Date.now() - started}ms: ${error.message}`);
            return {
                content: [{
                    type: "text",
                    text: `Search error: ${error.message}`
                }],
                isError: true
            };
        }
    }
);

// === TOOL 3: JSON to CSV ===
server.registerTool(
    "json_to_csv",
    {
        title: "JSON to CSV",
        description:
            "Convert JSON data into CSV text.\n\n" +
            "Use this tool when:\n" +
            "- The user asks to convert JSON to CSV or to put JSON data in a spreadsheet-friendly format\n" +
            "- The user provides an array of objects (or a single object) and wants a table\n\n" +
            "Behavior:\n" +
            "- Accepts a JSON string containing an array of objects or a single object (a single object becomes one row)\n" +
            "- Nested objects are flattened into columns using dot notation (e.g. address.city) by default\n" +
            "- Nested arrays are kept as JSON strings in a single cell\n" +
            "- Column headers are the union of keys across all rows; missing values are left empty\n" +
            "- Values containing delimiters, quotes, or newlines are properly escaped\n\n" +
            "Returns the CSV as text.",
        inputSchema: {
            json: z.string().describe("A JSON string: an array of objects, or a single object"),
            delimiter: z
                .string()
                .length(1)
                .optional()
                .describe("Single-character field delimiter (default ','); pass an actual tab character for TSV"),
            include_header: z
                .boolean()
                .optional()
                .describe("Include a header row with column names (default true)"),
            flatten: z
                .boolean()
                .optional()
                .describe("Flatten nested objects into dot-notation columns (default true)"),
            fields: z
                .array(z.string())
                .optional()
                .describe("Optional list of columns to include, in order (dot notation for nested fields). Defaults to all fields."),
        },
    },
    async ({ json, delimiter = ",", include_header = true, flatten: doFlatten = true, fields }) => {
        const tag = "[json_to_csv]";
        log(`${tag} called, ${json.length} chars`);
        try {
            let data;
            try {
                data = JSON.parse(json);
            } catch (e) {
                throw new Error(`Invalid JSON: ${e.message}`);
            }

            if (data === null || typeof data !== "object") {
                throw new Error("JSON must be an array of objects or a single object");
            }
            if (Array.isArray(data) && data.some((row) => row === null || typeof row !== "object")) {
                throw new Error("Every array element must be an object");
            }

            const rows = Array.isArray(data) ? data : [data];
            if (rows.length === 0) {
                return { content: [{ type: "text", text: "Input array is empty; no CSV to produce." }] };
            }

            const opts = {
                delimiter,
                header: include_header,
                transforms: doFlatten ? [flatten({ objects: true, arrays: false, separator: "." })] : [],
            };
            if (fields && fields.length > 0) opts.fields = fields;

            const csv = new Parser(opts).parse(rows);
            log(`${tag} ok: ${rows.length} rows, ${csv.length} chars`);
            return { content: [{ type: "text", text: csv }] };
        } catch (error) {
            log(`${tag} error: ${error.message}`);
            return {
                content: [{ type: "text", text: `JSON to CSV error: ${error.message}` }],
                isError: true,
            };
        }
    }
);

// Start the server
async function main() {
    const transport = new StdioServerTransport();
    await server.connect(transport);
    log("lmstudio MCP server running on stdio");
}

if (require.main === module) {
    main().catch(console.error);
}

module.exports = {
    server,
    runCommand,
    decodeHtml,
    resolveResultUrl,
    resolveBingUrl,
    searchDuckDuckGoLite,
    searchBrave,
    searchBing,
    searchDuckDuckGoInstant,
    webSearch,
    main,
};

