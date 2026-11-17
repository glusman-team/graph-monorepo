#!/usr/bin/env bash
# npm audit, failing only on advisories NOT listed in scripts/npm-audit-allowlist.txt.
#
# npm (11.x) has no native per-advisory ignore, so this runs `npm audit --json`,
# collects every DIRECT advisory the report cites, and fails when one is not
# allowlisted. An advisory leaves the allowlist the moment its fix ships and the
# report stops citing it, so the list only ever blocks known-accepted risk.
#
# Override the allowlist location with ALLOWLIST (e.g. ALLOWLIST=/dev/null to
# prove the gate still bites).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
allowlist="${ALLOWLIST:-$root/scripts/npm-audit-allowlist.txt}"
report="$(mktemp)"
trap 'rm -f "$report"' EXIT

npm audit --json > "$report" || true
node - "$report" "$allowlist" <<'NODE'
const fs = require("fs");
const [reportPath, allowlistPath] = process.argv.slice(2);
const audit = JSON.parse(fs.readFileSync(reportPath, "utf8"));
const allowlist = fs
  .readFileSync(allowlistPath, "utf8")
  .split("\n")
  .map((line) => line.replace(/#.*$/, "").trim())
  .filter(Boolean);
const advisories = new Map();
for (const vuln of Object.values(audit.vulnerabilities ?? {})) {
  for (const via of vuln.via ?? []) {
    if (typeof via === "object" && via.url) advisories.set(via.url, via.title ?? via.url);
  }
}
if (audit.error) {
  console.error(`npm audit failed: ${JSON.stringify(audit.error)}`);
  process.exit(1);
}
const unallowed = [...advisories.keys()].filter(
  (url) => !allowlist.some((id) => url.includes(id)),
);
if (unallowed.length > 0) {
  console.error("npm audit found non-allowlisted advisories:");
  for (const url of unallowed) console.error(`  ${url} (${advisories.get(url)})`);
  process.exit(1);
}
console.log(
  `npm audit clean against the allowlist: ${advisories.size} advisory/advisories cited, all allowlisted.`,
);
NODE
