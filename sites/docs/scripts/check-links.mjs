import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(siteRoot, "dist");
const base = "/graph-monorepo";

async function filesBelow(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await filesBelow(target)));
    else files.push(target);
  }
  return files;
}

async function exists(target) {
  try {
    return (await stat(target)).isFile();
  } catch {
    return false;
  }
}

const files = await filesBelow(dist);
const htmlFiles = files.filter((file) => file.endsWith(".html"));
const errors = [];
let checkedLinks = 0;

for (const htmlFile of htmlFiles) {
  const html = await readFile(htmlFile, "utf8");
  for (const match of html.matchAll(/\s(?:href|src)=["']([^"']+)["']/g)) {
    const raw = match[1].replaceAll("&amp;", "&");
    if (/^(?:[a-z]+:|\/\/)/i.test(raw) || raw.startsWith("#")) continue;
    const [withoutFragment, fragment = ""] = raw.split("#", 2);
    const pathname = withoutFragment.split("?", 1)[0];
    if (!pathname) continue;

    let decoded;
    try {
      decoded = decodeURIComponent(pathname);
    } catch {
      errors.push(`${htmlFile}: invalid URL encoding in ${raw}`);
      continue;
    }

    let target;
    if (decoded === base || decoded.startsWith(`${base}/`)) {
      target = path.join(dist, decoded.slice(base.length));
    } else if (decoded.startsWith("/")) {
      target = path.join(dist, decoded);
    } else {
      target = path.resolve(path.dirname(htmlFile), decoded);
    }

    const candidates = decoded.endsWith("/")
      ? [path.join(target, "index.html")]
      : path.extname(target)
        ? [target]
        : [path.join(target, "index.html"), `${target}.html`];
    let resolved = null;
    for (const candidate of candidates) {
      if (await exists(candidate)) {
        resolved = candidate;
        break;
      }
    }
    checkedLinks += 1;
    if (!resolved) {
      errors.push(`${path.relative(dist, htmlFile)}: missing ${raw}`);
      continue;
    }

    if (fragment && resolved.endsWith(".html")) {
      const targetHtml = await readFile(resolved, "utf8");
      const ids = new Set(
        [...targetHtml.matchAll(/\sid=["']([^"']+)["']/g)].map((entry) => entry[1]),
      );
      if (!ids.has(decodeURIComponent(fragment))) {
        errors.push(`${path.relative(dist, htmlFile)}: missing fragment ${raw}`);
      }
    }
  }
}

const homepage = await readFile(path.join(dist, "index.html"), "utf8");
for (const text of [
  "Use approved-treatment data",
  "Read a relationship in Linkouts",
  "Build or publish a release",
  "Add a knowledge graph",
  "Operate and deploy",
  "Change the code",
]) {
  if (!homepage.includes(text)) errors.push(`index.html: missing intent route ${text}`);
}

for (const required of [
  "404.html",
  "dakp/index.html",
  "linkouts/index.html",
  "linkouts/operations/upload/index.html",
  "linkouts/display/config-reference/index.html",
  "pagefind/pagefind.js",
]) {
  if (!(await exists(path.join(dist, required)))) errors.push(`missing build output ${required}`);
}

if (htmlFiles.length < 18)
  errors.push(`expected at least 18 HTML pages, found ${htmlFiles.length}`);
if (errors.length) {
  console.error(errors.join("\n"));
  process.exitCode = 1;
} else {
  console.log(`Checked ${checkedLinks} local links across ${htmlFiles.length} generated pages.`);
}
