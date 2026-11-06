import { copyFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const notFoundDirectory = path.join(siteRoot, "dist", "404");

await copyFile(path.join(notFoundDirectory, "index.html"), path.join(siteRoot, "dist", "404.html"));
await rm(notFoundDirectory, { recursive: true, force: true });
