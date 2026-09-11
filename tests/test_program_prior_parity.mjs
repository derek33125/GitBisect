import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { rankBySignals } = require(
  path.join(
    here,
    "..",
    "human_analysis",
    "human_analysis-20260905",
    "human_analysis",
    "dep-analysis.js",
  ),
);
const fixture = JSON.parse(
  fs.readFileSync(
    path.join(here, "fixtures", "ceg_program_prior_golden.json"),
    "utf8",
  ),
);

const shas = fixture.candidates.map((item) => item.sha);
const sizes = new Map(
  fixture.candidates.map((item) => [item.sha, item.changed_file_count]),
);
const signals = fixture.signals.map((item) => [
  item.name,
  new Set(item.members),
  item.group,
]);
const scored = rankBySignals(shas, signals, shas.length, sizes, 0);
const bySha = new Map(scored.map((item, index) => [item.sha, { ...item, rank: index + 1 }]));

for (const expected of fixture.expected) {
  const actual = bySha.get(expected.sha);
  assert.ok(actual, `missing ${expected.sha}`);
  assert.ok(
    Math.abs(actual.score - expected.score_without_floor) < 1e-12,
    `score drift for ${expected.sha}: ${actual.score}`,
  );
  assert.equal(actual.rank, expected.rank, `rank drift for ${expected.sha}`);
  assert.deepEqual(actual.hit, expected.hits, `hit drift for ${expected.sha}`);
}

const channelCoverage = {};
for (const channel of Object.keys(fixture.expected_channel_coverage)) {
  channelCoverage[channel] = new Set(
    fixture.signals
      .filter((item) => item.channel === channel)
      .flatMap((item) => item.members),
  ).size;
}
assert.deepEqual(channelCoverage, fixture.expected_channel_coverage);

console.log("CEG program-prior JS/Python golden parity fixture passed");
