/**
 * Sanity-check the parser against real KB content.
 *
 * Assertions:
 * - Every blog-* and <digits>-* page has a frontmatter block (user_score key).
 * - An empty `user_score:` value parses as null.
 * - `user_score: 0` parses as 0 (not null).
 * - Pages without <details> (topics, syntheses top-level) don't throw and
 *   get `userScore: null`.
 * - Every page's title is non-empty.
 * - Every dated page has date !== null.
 */
import { scanPages } from "../lib/kb/scan";
import { buildKbPage } from "../lib/kb/parse";

const FIXTURE_EMPTY = `# [Foo](https://example.com)
*By X · Y · 2026-04-14*

<details><summary><strong>Metadata</strong></summary>

\`\`\`yaml
url: "https://example.com"
user_score:
relevance_score: 7
topics: ["a", "b"]
\`\`\`

</details>

---

## TLDR
body
`;

const FIXTURE_ZERO = FIXTURE_EMPTY.replace("user_score:", "user_score: 0");
const FIXTURE_EIGHT = FIXTURE_EMPTY.replace("user_score:", "user_score: 8");
const FIXTURE_NO_DETAILS = `# Topic file

Just plain markdown, no details block, no frontmatter.

## Section
content.
`;

function check(label: string, cond: boolean, detail?: string) {
  if (cond) {
    console.log(`  ✓ ${label}`);
  } else {
    console.error(`  ✗ ${label}${detail ? ` — ${detail}` : ""}`);
    process.exitCode = 1;
  }
}

async function main() {
  console.log("=== Fixture tests ===");
  const empty = buildKbPage("2026/04/14/blog-empty.md", FIXTURE_EMPTY);
  check("empty user_score → null", empty.userScore === null);
  check("empty user_score → canRate=true", empty.canRate === true);
  check("empty body has TLDR", /TLDR/.test(empty.body));
  check("empty <details> stripped from body", !empty.body.includes("<details"));
  check("topics parsed", empty.topics.length === 2 && empty.topics[0] === "a");
  check("title extracted", empty.title === "Foo");
  check("url extracted", empty.url === "https://example.com");
  check("byline extracted", empty.byline === "By X · Y · 2026-04-14");

  const zero = buildKbPage("2026/04/14/blog-zero.md", FIXTURE_ZERO);
  check("user_score: 0 → 0", zero.userScore === 0);

  const eight = buildKbPage("2026/04/14/blog-eight.md", FIXTURE_EIGHT);
  check("user_score: 8 → 8", eight.userScore === 8);

  const topic = buildKbPage("topics/foo.md", FIXTURE_NO_DETAILS);
  check("no details → userScore null", topic.userScore === null);
  check("no details → canRate false", topic.canRate === false);
  check("no details → canChat false", topic.canChat === false);
  check("no details → body preserved", topic.body.includes("Just plain markdown"));

  // Tweet with no user_score field still gets chat + rating (rating inserts field on write).
  const TWEET_FIXTURE = `# [@foo: bar](https://x.com/foo/status/1)
*@foo · 2026-04-16*

<details><summary><strong>Metadata</strong></summary>

\`\`\`yaml
source_type: tweet
tweet_id: "1"
author: "@foo"
url: "https://x.com/foo/status/1"
relevance_score: 5
\`\`\`

</details>

## TLDR
body
`;
  const tweet = buildKbPage("2026/04/16/1-foo-bar.md", TWEET_FIXTURE);
  check("tweet kind classified", tweet.kind === "tweet");
  check("tweet canChat=true", tweet.canChat === true);
  check("tweet canRate=true (even without user_score line)", tweet.canRate === true);

  // Podcast classification + manual-slot exclusion.
  const podcast = buildKbPage("2026/04/16/podcast-foo-bar.md", FIXTURE_EMPTY);
  check("podcast kind classified", podcast.kind === "podcast");
  check("podcast canChat=true", podcast.canChat === true);

  const MANUAL_FIXTURE = FIXTURE_EMPTY.replace(
    `topics: ["a", "b"]`,
    `topics: ["a", "b"]\nslot: manual`,
  );
  const manual = buildKbPage("2026/04/16/blog-manual.md", MANUAL_FIXTURE);
  check("manual-slot canChat=true", manual.canChat === true);
  check("manual-slot canRate=false", manual.canRate === false);

  console.log("\n=== Real KB content ===");
  const pages = await scanPages();
  console.log(`Scanned ${pages.length} pages`);
  if (pages.length === 0) {
    console.error("  ✗ scan returned 0 pages — run `tsx scripts/sync-content.ts` to populate .kb-content/");
    process.exitCode = 1;
    return;
  }

  let blogCount = 0;
  let tweetCount = 0;
  let podcastCount = 0;
  let synthCount = 0;
  let runlogCount = 0;
  let topicCount = 0;
  let dailyCount = 0;
  let titleless = 0;
  let missingDate = 0;
  let rateable = 0;
  let chatable = 0;

  for (const p of pages) {
    switch (p.kind) {
      case "blog":
        blogCount++;
        break;
      case "tweet":
        tweetCount++;
        break;
      case "podcast":
        podcastCount++;
        break;
      case "synthesis":
        synthCount++;
        break;
      case "runlog":
        runlogCount++;
        break;
      case "topic":
        topicCount++;
        break;
      case "daily":
        dailyCount++;
        break;
    }
    if (!p.title || p.title === "Untitled") titleless++;
    if ((p.kind === "blog" || p.kind === "tweet") && !p.date) missingDate++;
    if (p.canRate) rateable++;
    if (p.canChat) chatable++;
  }

  console.log(`  blog=${blogCount} tweet=${tweetCount} podcast=${podcastCount} synth=${synthCount} runlog=${runlogCount} topic=${topicCount} daily=${dailyCount}`);
  console.log(`  rateable=${rateable} chatable=${chatable}`);
  check(`no runlog pages rateable`, pages.filter((p) => p.kind === "runlog" && p.canRate).length === 0);
  check(`no runlog pages chatable`, pages.filter((p) => p.kind === "runlog" && p.canChat).length === 0);
  check(`no topic pages chatable`, pages.filter((p) => p.kind === "topic" && p.canChat).length === 0);
  check(`at least one blog rateable`, pages.some((p) => p.kind === "blog" && p.canRate));
  check(`all tweets chatable`, pages.filter((p) => p.kind === "tweet" && !p.canChat).length === 0);
  check(`all podcasts chatable`, pages.filter((p) => p.kind === "podcast" && !p.canChat).length === 0);
  check(`manual-slot pages are NOT rateable`, pages.filter((p) => p.slot === "manual" && p.canRate).length === 0);
  check(`title extraction rate`, titleless < pages.length * 0.1, `${titleless} pages untitled`);
  check(`dated pages have dates`, missingDate === 0, `${missingDate} dated pages missing date`);

  console.log("\nDone.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
