const { test, describe, before, after, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

// Must be set before the module loads so tests never write to the real log.
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "lmstudio-"));
process.env.LMSTUDIO_LOG_FILE = path.join(tmpDir, "test.log");

const { Client } = require("@modelcontextprotocol/sdk/client/index.js");
const { InMemoryTransport } = require("@modelcontextprotocol/sdk/inMemory.js");
const {
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
} = require("./lmstudio.js");

const originalFetch = global.fetch;
const originalConsoleError = console.error;

let client;

before(async () => {
    console.error = () => {}; // silence server log mirroring
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    client = new Client({ name: "test-client", version: "1.0.0" });
    await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
});

after(async () => {
    console.error = originalConsoleError;
    await client.close();
    fs.rmSync(tmpDir, { recursive: true, force: true });
});

afterEach(() => {
    global.fetch = originalFetch;
});

const call = (name, args = {}) => client.callTool({ name, arguments: args });
const textOf = (result) => result.content[0].text;

function ddgHtml(results) {
    const rows = results
        .map(
            (r) =>
                `<tr><td><a rel="nofollow" href="${r.href}" class='result-link'>${r.title}</a></td></tr>` +
                `<tr><td class='result-snippet'>${r.snippet}</td></tr>`,
        )
        .join("\n");
    return `<html><body><table>${rows}</table></body></html>`;
}

// Mirrors the structure of search.brave.com result blocks (stable classes + svelte hash classes).
function braveHtml(results) {
    return (
        "<html><body>" +
        `<div class="snippet svelte-abc" data-pos="0" data-type="news"><a href="https://news.example/">News</a></div>` +
        results
            .map(
                (r, i) =>
                    `<div class="snippet svelte-abc" data-pos="${i}" data-type="web" data-keynav="true"><div class="result-content svelte-1">` +
                    `<a href="${r.url}" target="_self" class="svelte-14r20fy l1"><div class="site-name-wrapper"><cite>site.com</cite></div>` +
                    `<div class="title search-snippet-title line-clamp-1 svelte-14r20fy" title="x">${r.title}</div></a>` +
                    `<div class="generic-snippet svelte-1"><div class="content desktop-default-regular t-primary svelte-1"><!---->${r.snippet}<!----></div></div>` +
                    `</div></div>`,
            )
            .join("") +
        "</body></html>"
    );
}

const bingTrackingUrl = (target) =>
    `https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;ptn=3&amp;u=a1${Buffer.from(target).toString("base64url")}&amp;ntb=1`;

function bingHtml(results) {
    return (
        "<html><body><ol>" +
        results
            .map(
                (r) =>
                    `<li class="b_algo" data-id><div class="b_tpcn"><a class="tilk" href="${bingTrackingUrl("https://nav.example/")}">nav</a></div>` +
                    `<h2><a target="_blank" href="${bingTrackingUrl(r.url)}">${r.title}</a></h2>` +
                    `<div class="b_caption"><p class="b_lineclamp2">${r.snippet}</p></div></li>`,
            )
            .join("") +
        "</ol></body></html>"
    );
}

function mockFetch(body, { status = 200 } = {}) {
    const calls = [];
    global.fetch = async (url, init) => {
        calls.push({ url, init });
        return new Response(body, { status });
    };
    return calls;
}

/**
 * Route fetches by source. Each value is a body string, or { body, status }.
 * Unlisted sources answer HTTP 500. Returns the list of sources requested, in order.
 */
function routeFetch({ brave, bing, ddgLite, instant }) {
    const hits = [];
    const sources = [
        ["brave", "search.brave.com", brave],
        ["bing", "www.bing.com", bing],
        ["ddgLite", "lite.duckduckgo.com", ddgLite],
        ["instant", "api.duckduckgo.com", instant],
    ];
    global.fetch = async (url) => {
        const [name, , spec] = sources.find(([, host]) => String(url).includes(host));
        hits.push(name);
        if (spec === undefined) return new Response("unavailable", { status: 500 });
        const { body, status = 200 } = typeof spec === "string" ? { body: spec } : spec;
        return new Response(body, { status });
    };
    return hits;
}

describe("server registration", () => {
    test("lists the three tools", async () => {
        const { tools } = await client.listTools();
        assert.deepEqual(tools.map((t) => t.name).sort(), ["get_current_date", "json_to_csv", "web_search"]);
    });

    test("every tool has a description", async () => {
        const { tools } = await client.listTools();
        for (const tool of tools) assert.ok(tool.description && tool.description.length > 20, tool.name);
    });
});

describe("runCommand", () => {
    test("returns stdout and stderr", async () => {
        const { stdout, stderr } = await runCommand("echo out; echo err >&2");
        assert.equal(stdout, "out\n");
        assert.equal(stderr, "err\n");
    });

    test("rejects on non-zero exit with the code and stderr", async () => {
        await assert.rejects(runCommand("echo boom >&2; exit 3"), /code 3: boom/);
    });
});

describe("decodeHtml", () => {
    test("strips tags and decodes entities", () => {
        assert.equal(decodeHtml("<b>Tom</b> &amp; Jerry &lt;3 &quot;x&quot; &#39;y&#x27;"), `Tom & Jerry <3 "x" 'y'`);
    });

    test("collapses whitespace and non-breaking spaces", () => {
        assert.equal(decodeHtml("  a&nbsp;&nbsp;b \n\t c  "), "a b c");
    });

    test("decodes numeric entities (decimal and hex)", () => {
        assert.equal(decodeHtml("Node.js&#174; &#0183; &#x2019;s"), "Node.js\u00ae \u00b7 \u2019s");
    });

    test("does not double-decode &amp;", () => {
        assert.equal(decodeHtml("&amp;lt;b&amp;gt;"), "&lt;b&gt;");
    });
});

describe("resolveResultUrl", () => {
    test("extracts the uddg target from a DDG redirect", () => {
        const href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&amp;rut=abc";
        assert.equal(resolveResultUrl(href), "https://example.com/a?b=1");
    });

    test("returns direct links unchanged", () => {
        assert.equal(resolveResultUrl("https://example.com/page"), "https://example.com/page");
    });
});

describe("searchDuckDuckGoLite", () => {
    test("posts the query and parses results", async () => {
        const calls = mockFetch(
            ddgHtml([
                { href: "//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2F", title: "A &amp; <b>One</b>", snippet: "First <b>snippet</b>" },
                { href: "https://b.com/", title: "Two", snippet: "Second" },
            ]),
        );
        const results = await searchDuckDuckGoLite("hello world");

        assert.equal(calls.length, 1);
        assert.equal(calls[0].init.method, "POST");
        assert.equal(new URLSearchParams(calls[0].init.body).get("q"), "hello world");
        assert.deepEqual(results, [
            { title: "A & One", url: "https://a.com/", description: "First snippet" },
            { title: "Two", url: "https://b.com/", description: "Second" },
        ]);
    });

    test("sends the browser headers DDG Lite expects", async () => {
        const calls = mockFetch(ddgHtml([{ href: "https://a.com/", title: "A", snippet: "s" }]));
        await searchDuckDuckGoLite("q");
        const h = calls[0].init.headers;
        assert.equal(h["Content-Type"], "application/x-www-form-urlencoded");
        assert.equal(h.Origin, "https://lite.duckduckgo.com");
        assert.equal(h.Referer, "https://lite.duckduckgo.com/");
        assert.equal(h["Sec-Ch-Ua"], '"Not-A.Brand";v="99", "Google Chrome";v="157", "Chromium";v="157"');
        assert.equal(h["Sec-Ch-Ua-Mobile"], "?0");
        assert.equal(h["Sec-Ch-Ua-Platform"], '"macOS"');
        assert.equal(h["Upgrade-Insecure-Requests"], "1");
        assert.match(h["User-Agent"], /Chrome\/157\.0\.0\.0 Safari\/537\.36$/);
    });

    test("filters out sponsored duckduckgo.com/y.js results", async () => {
        mockFetch(
            ddgHtml([
                { href: "https://duckduckgo.com/y.js?ad=1", title: "Ad", snippet: "buy" },
                { href: "https://real.com/", title: "Real", snippet: "ok" },
            ]),
        );
        const results = await searchDuckDuckGoLite("q");
        assert.deepEqual(results.map((r) => r.url), ["https://real.com/"]);
    });

    test("throws on CAPTCHA page", async () => {
        mockFetch('<div class="anomaly-modal">captcha</div>');
        await assert.rejects(searchDuckDuckGoLite("q"), /CAPTCHA/);
    });

    test("throws on HTTP errors", async () => {
        mockFetch("nope", { status: 503 });
        await assert.rejects(searchDuckDuckGoLite("q"), /HTTP 503/);
    });
});

describe("searchBrave", () => {
    test("requests the query and parses web results only", async () => {
        const calls = mockFetch(
            braveHtml([
                { url: "https://a.com/x", title: "A &amp; <b>One</b>", snippet: "First <strong>snippet</strong>" },
                { url: "https://search.brave.com/internal", title: "Internal", snippet: "skip me" },
                { url: "https://b.com/", title: "Two", snippet: "Second" },
            ]),
        );
        const results = await searchBrave("hello world");

        assert.equal(new URL(calls[0].url).hostname, "search.brave.com");
        assert.equal(new URL(calls[0].url).searchParams.get("q"), "hello world");
        assert.deepEqual(results, [
            { title: "A & One", url: "https://a.com/x", description: "First snippet" },
            { title: "Two", url: "https://b.com/", description: "Second" },
        ]);
    });

    test("returns an empty list for pages without results", async () => {
        mockFetch("<html></html>");
        assert.deepEqual(await searchBrave("q"), []);
    });

    test("throws on rate limiting", async () => {
        mockFetch("slow down", { status: 429 });
        await assert.rejects(searchBrave("q"), /HTTP 429/);
    });
});

describe("resolveBingUrl", () => {
    test("decodes the real target from a Bing click-tracking link", () => {
        assert.equal(resolveBingUrl(bingTrackingUrl("https://example.com/a?b=1&c=2")), "https://example.com/a?b=1&c=2");
    });

    test("returns direct links unchanged", () => {
        assert.equal(resolveBingUrl("https://example.com/page"), "https://example.com/page");
    });

    test("falls back to the raw link when the target cannot be decoded", () => {
        const href = "https://www.bing.com/ck/a?u=notbase64";
        assert.equal(resolveBingUrl(href), href);
    });
});

describe("searchBing", () => {
    test("parses titles, decoded URLs, and snippets", async () => {
        const calls = mockFetch(
            bingHtml([
                { url: "https://a.com/", title: "Download Node.js&#174;", snippet: "5 days ago&nbsp;&#0183;&#32;First" },
                { url: "https://b.com/p?q=1", title: "Two", snippet: "Second" },
            ]),
        );
        const results = await searchBing("node");

        assert.equal(new URL(calls[0].url).hostname, "www.bing.com");
        assert.equal(new URL(calls[0].url).searchParams.get("q"), "node");
        assert.deepEqual(results, [
            { title: "Download Node.js\u00ae", url: "https://a.com/", description: "5 days ago \u00b7 First" },
            { title: "Two", url: "https://b.com/p?q=1", description: "Second" },
        ]);
    });

    test("throws on HTTP errors", async () => {
        mockFetch("nope", { status: 503 });
        await assert.rejects(searchBing("q"), /HTTP 503/);
    });
});

describe("searchDuckDuckGoInstant", () => {
    const answer = {
        Heading: "Node.js",
        AbstractText: "Node.js is a runtime.",
        AbstractURL: "https://en.wikipedia.org/wiki/Node.js",
        Results: [{ FirstURL: "https://nodejs.org/", Text: "Official site" }],
        RelatedTopics: [
            { FirstURL: "https://duckduckgo.com/c/Joyent", Text: "Joyent" },
            { FirstURL: "https://duckduckgo.com/Deno", Text: "Deno - A JavaScript runtime" },
            { Name: "Group", Topics: [{ FirstURL: "https://duckduckgo.com/Bun", Text: "Bun - Another runtime" }] },
        ],
    };

    test("returns the abstract, results, and related topics (skipping category pages)", async () => {
        mockFetch(JSON.stringify(answer));
        const results = await searchDuckDuckGoInstant("node");
        assert.deepEqual(results, [
            { title: "Node.js", url: "https://en.wikipedia.org/wiki/Node.js", description: "Node.js is a runtime." },
            { title: "Official site", url: "https://nodejs.org/", description: "" },
            { title: "Deno", url: "https://duckduckgo.com/Deno", description: "A JavaScript runtime" },
            { title: "Bun", url: "https://duckduckgo.com/Bun", description: "Another runtime" },
        ]);
    });

    test("returns an empty list when there is no instant answer", async () => {
        mockFetch(JSON.stringify({ AbstractText: "", Results: [], RelatedTopics: [] }));
        assert.deepEqual(await searchDuckDuckGoInstant("zzz"), []);
    });
});

describe("webSearch (fallback chain)", () => {
    const src = (name, fn) => ({ name, search: fn });
    const ok = (title) => async () => [{ title, url: `https://${title}.com/`, description: "" }];

    test("uses the first source that returns results and stops", async () => {
        const calls = [];
        const sources = [
            src("A", async () => (calls.push("A"), [])),
            src("B", async () => (calls.push("B"), ok("b")())),
            src("C", async () => (calls.push("C"), ok("c")())),
        ];
        const { source, results } = await webSearch("q", sources);
        assert.equal(source, "B");
        assert.equal(results[0].title, "b");
        assert.deepEqual(calls, ["A", "B"]);
    });

    test("falls through errors to a working source", async () => {
        const sources = [
            src("A", async () => {
                throw new Error("boom");
            }),
            src("B", ok("b")),
        ];
        assert.equal((await webSearch("q", sources)).source, "B");
    });

    test("throws with every failure listed when all sources error", async () => {
        const sources = [
            src("A", async () => {
                throw new Error("boom");
            }),
            src("B", async () => {
                throw new Error("bang");
            }),
        ];
        await assert.rejects(webSearch("q", sources), /All search sources failed\. A: boom; B: bang/);
    });

    test("returns empty results (no error) when sources answer but find nothing", async () => {
        const sources = [
            src("A", async () => {
                throw new Error("boom");
            }),
            src("B", async () => []),
        ];
        assert.deepEqual(await webSearch("q", sources), { source: null, results: [] });
    });

    test("skips a rate-limited source for a while", async () => {
        let limitedCalls = 0;
        const limited = src("Limited", async () => {
            limitedCalls++;
            throw new Error("HTTP 429");
        });
        const sources = [limited, src("B", ok("b"))];

        assert.equal((await webSearch("q1", sources)).source, "B");
        assert.equal((await webSearch("q2", sources)).source, "B");
        assert.equal(limitedCalls, 1);
    });

    test("does not put a source on cooldown for ordinary errors", async () => {
        let calls = 0;
        const flaky = src("Flaky", async () => {
            calls++;
            throw new Error("HTTP 500");
        });
        const sources = [flaky, src("B", ok("b"))];
        await webSearch("q1", sources);
        await webSearch("q2", sources);
        assert.equal(calls, 2);
    });
});

describe("get_current_date", () => {
    test("returns the system date", async () => {
        const result = await call("get_current_date");
        assert.ok(!result.isError);
        assert.match(textOf(result), /^Current date\/time: .+/);
        const year = String(new Date().getFullYear());
        assert.ok(textOf(result).includes(year));
    });
});

describe("web_search tool", () => {
    // Splits "Search provider: X\n\n<results>" into the provider name and the result blocks.
    const parse = (result) => {
        const [header, ...blocks] = textOf(result).split("\n\n");
        const match = header.match(/^Search provider: (.+)(?:\nSkipped providers: (.+))?$/);
        assert.ok(match, `missing provider header in: ${header}`);
        return { provider: match[1], skipped: match[2], blocks };
    };

    const items = Array.from({ length: 8 }, (_, i) => ({
        url: `https://site${i + 1}.com/`,
        title: `Title ${i + 1}`,
        snippet: `Snippet ${i + 1}`,
    }));
    const sample = ddgHtml(items.map((r) => ({ href: r.url, title: r.title, snippet: r.snippet })));

    test("formats results and defaults to 5", async () => {
        routeFetch({ ddgLite: sample });
        const result = await call("web_search", { query: "test" });
        assert.ok(!result.isError);
        const { provider, skipped, blocks } = parse(result);
        assert.equal(provider, "DuckDuckGo Lite");
        assert.equal(skipped, undefined); // nothing was skipped, so no "Skipped providers" line
        assert.equal(blocks.length, 5);
        assert.equal(blocks[0], "1. Title 1\nhttps://site1.com/\nSnippet 1");
    });

    test("respects max_results", async () => {
        routeFetch({ ddgLite: sample });
        const result = await call("web_search", { query: "test", max_results: 2 });
        assert.equal(parse(result).blocks.length, 2);
    });

    test("uses DuckDuckGo Lite first and does not touch other sources when it succeeds", async () => {
        const hits = routeFetch({ ddgLite: sample, brave: braveHtml(items), bing: bingHtml(items) });
        await call("web_search", { query: "test" });
        assert.deepEqual(hits, ["ddgLite"]);
    });

    test("falls back to Brave when DuckDuckGo Lite fails", async () => {
        const hits = routeFetch({ brave: braveHtml(items) });
        const result = await call("web_search", { query: "test" });
        assert.ok(!result.isError);
        assert.deepEqual(hits, ["ddgLite", "brave"]);
        const { provider, skipped, blocks } = parse(result);
        assert.equal(provider, "Brave");
        assert.equal(skipped, "DuckDuckGo Lite (HTTP 500)");
        assert.equal(blocks[0], "1. Title 1\nhttps://site1.com/\nSnippet 1");
    });

    test("falls back past a DuckDuckGo Lite page with no parseable results", async () => {
        const hits = routeFetch({ ddgLite: "<html>layout changed</html>", brave: braveHtml(items) });
        const result = await call("web_search", { query: "test" });
        assert.ok(!result.isError);
        assert.deepEqual(hits, ["ddgLite", "brave"]);
    });

    test("falls back to Bing, then the Instant Answer API", async () => {
        let hits = routeFetch({ bing: bingHtml(items) });
        let result = await call("web_search", { query: "test" });
        assert.deepEqual(hits, ["ddgLite", "brave", "bing"]);
        assert.equal(parse(result).provider, "Bing");
        assert.equal(parse(result).skipped, "DuckDuckGo Lite (HTTP 500); Brave (HTTP 500)");
        assert.equal(parse(result).blocks[0], "1. Title 1\nhttps://site1.com/\nSnippet 1");

        hits = routeFetch({
            instant: JSON.stringify({ AbstractText: "About it", AbstractURL: "https://wiki.example/it", Heading: "It" }),
        });
        result = await call("web_search", { query: "test" });
        assert.deepEqual(hits, ["ddgLite", "brave", "bing", "instant"]);
        assert.equal(
            textOf(result),
            "Search provider: DuckDuckGo Instant\n" +
                "Skipped providers: DuckDuckGo Lite (HTTP 500); Brave (HTTP 500); Bing (HTTP 500)\n\n" +
                "1. It\nhttps://wiki.example/it\nAbout it",
        );
    });

    test("reports when nothing is found", async () => {
        routeFetch({ brave: "<html></html>", bing: "<html></html>", ddgLite: "<html></html>", instant: "{}" });
        const result = await call("web_search", { query: "zzz" });
        assert.ok(!result.isError);
        assert.equal(textOf(result), 'No results found for "zzz".');
    });

    test("returns isError listing every source when all fail", async () => {
        routeFetch({});
        const result = await call("web_search", { query: "test" });
        assert.equal(result.isError, true);
        assert.match(textOf(result), /Search error: All search sources failed\./);
        for (const name of ["DuckDuckGo Lite", "Brave", "Bing", "DuckDuckGo Instant"]) {
            assert.match(textOf(result), new RegExp(`${name}: HTTP 500`));
        }
    });

    test("returns isError on network failure, including the underlying cause", async () => {
        global.fetch = async () => {
            const error = new TypeError("fetch failed");
            error.cause = { code: "ENOTFOUND" };
            throw error;
        };
        const result = await call("web_search", { query: "test" });
        assert.equal(result.isError, true);
        assert.match(textOf(result), /fetch failed \(ENOTFOUND\)/);
    });

    test("rejects out-of-range max_results at the schema level", async () => {
        routeFetch({ ddgLite: sample });
        const result = await call("web_search", { query: "test", max_results: 11 }).catch((e) => ({ isError: true, text: e.message }));
        assert.equal(result.isError, true);
    });
});

describe("json_to_csv tool", () => {
    const csv = async (args) => {
        const result = await call("json_to_csv", args);
        return { result, text: textOf(result) };
    };
    const lines = (text) => text.split(/\r?\n/);

    test("converts an array of objects", async () => {
        const { result, text } = await csv({ json: JSON.stringify([{ a: 1, b: "x" }, { a: 2, b: "y" }]) });
        assert.ok(!result.isError);
        assert.deepEqual(lines(text), ['"a","b"', '1,"x"', '2,"y"']);
    });

    test("treats a single object as one row", async () => {
        const { text } = await csv({ json: '{"a":1,"b":2}' });
        assert.deepEqual(lines(text), ['"a","b"', "1,2"]);
    });

    test("uses the union of keys with empty cells for missing values", async () => {
        const { text } = await csv({ json: '[{"a":1},{"b":2}]' });
        assert.deepEqual(lines(text), ['"a","b"', "1,", ",2"]);
    });

    test("flattens nested objects with dot notation by default", async () => {
        const { text } = await csv({ json: '[{"name":"A","address":{"city":"X","zip":"1"}}]' });
        assert.equal(lines(text)[0], '"name","address.city","address.zip"');
    });

    test("does not flatten when flatten=false", async () => {
        const { text } = await csv({ json: '[{"address":{"city":"X"}}]', flatten: false });
        assert.equal(lines(text)[0], '"address"');
        assert.ok(text.includes('{""city"":""X""}'));
    });

    test("keeps nested arrays as JSON in one cell", async () => {
        const { text } = await csv({ json: '[{"tags":["a","b"]}]' });
        assert.equal(lines(text)[0], '"tags"');
        assert.ok(text.includes('[""a"",""b""]'));
    });

    test("supports custom delimiter (tab)", async () => {
        const { text } = await csv({ json: '[{"a":1,"b":2}]', delimiter: "\t" });
        assert.equal(lines(text)[1], "1\t2");
    });

    test("can omit the header", async () => {
        const { text } = await csv({ json: '[{"a":1,"b":2}]', include_header: false });
        assert.equal(text, "1,2");
    });

    test("honors the fields selection and order", async () => {
        const { text } = await csv({ json: '[{"a":1,"b":2,"c":3}]', fields: ["c", "a"] });
        assert.deepEqual(lines(text), ['"c","a"', "3,1"]);
    });

    test("escapes quotes, delimiters, and newlines", async () => {
        const { text } = await csv({ json: JSON.stringify([{ v: 'say "hi", ok\nbye' }]) });
        assert.ok(text.includes('"say ""hi"", ok\nbye"'));
    });

    test("reports an empty array without error", async () => {
        const { result, text } = await csv({ json: "[]" });
        assert.ok(!result.isError);
        assert.match(text, /empty/i);
    });

    test("rejects invalid JSON", async () => {
        const { result, text } = await csv({ json: "{nope" });
        assert.equal(result.isError, true);
        assert.match(text, /Invalid JSON/);
    });

    test("rejects scalars and null", async () => {
        for (const json of ["42", '"str"', "null"]) {
            const { result, text } = await csv({ json });
            assert.equal(result.isError, true, json);
            assert.match(text, /array of objects or a single object/);
        }
    });

    test("rejects arrays containing non-objects", async () => {
        const { result, text } = await csv({ json: '[{"a":1}, 2]' });
        assert.equal(result.isError, true);
        assert.match(text, /Every array element must be an object/);
    });
});
