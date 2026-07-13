// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — fail-closed Marketplace/Open VSX package policy

import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { unzipSync } from "fflate";
import { PNG } from "pngjs";

const decoder = new TextDecoder("utf-8", { fatal: true });
const requiredEntries = [
  "extension/package.json",
  "extension/readme.md",
  "extension/changelog.md",
  "extension/SUPPORT.md",
  "extension/PRIVACY.md",
  "extension/LICENSE.txt",
  "extension/media/icon.png",
  "extension/media/marketplace-board.png",
  "extension/media/marketplace-claims.png",
  "extension/out/extension.js",
];
const listingCommitPattern = "[0-9a-f]{40}";
const secretFile = /(?:^|\/)(?:\.npmrc|\.pypirc|\.netrc|\.env(?:\.[^/]*)?|id_(?:rsa|dsa|ecdsa|ed25519)|credentials?(?:\.[^/]*)?|secrets?(?:\.[^/]*)?|[^/]+\.(?:pem|key|p12|pfx|kdbx|token))$/i;
const secretTokenPatterns = [
  /-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----/,
  /\bAKIA[0-9A-Z]{16}\b/,
  /\bgh[pousr]_[A-Za-z0-9]{36,255}\b/,
  /\bgithub_pat_[A-Za-z0-9_]{40,255}\b/,
  /\bnpm_[A-Za-z0-9]{36,255}\b/,
  /\bxox[baprs]-[A-Za-z0-9-]{20,255}\b/,
  /\bAIza[0-9A-Za-z_-]{35}\b/,
  /\bsk-(?:ant-[A-Za-z0-9_-]{20,255}|proj-[A-Za-z0-9_-]{20,255}|[A-Za-z0-9]{32,255})\b/,
];
const binaryEntry = /\.(?:png)$/i;

function requireCondition(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function entryText(entries, name) {
  const value = entries[name];
  requireCondition(value !== undefined, `Missing required VSIX entry: ${name}`);
  return decoder.decode(value);
}

function parseManifest(entries) {
  let parsed;
  try {
    parsed = JSON.parse(entryText(entries, "extension/package.json"));
  } catch (error) {
    throw new Error(`Invalid packaged extension manifest: ${String(error)}`);
  }
  requireCondition(parsed !== null && typeof parsed === "object" && !Array.isArray(parsed), "Packaged manifest must be an object.");
  return parsed;
}

export function readPngDimensions(bytes, label = "PNG") {
  try {
    const image = PNG.sync.read(Buffer.from(bytes), { checkCRC: true });
    requireCondition(image.width > 0 && image.height > 0, `${label} has invalid dimensions.`);
    return { width: image.width, height: image.height };
  } catch (error) {
    throw new Error(`${label} is not a complete valid PNG: ${String(error)}`);
  }
}

function requireHttps(value, label) {
  requireCondition(typeof value === "string" && value.startsWith("https://"), `${label} must use HTTPS.`);
}

function validateManifest(manifest) {
  requireCondition(manifest.name === "synapse-channel-vscode", "Unexpected extension name.");
  requireCondition(manifest.publisher === "anulum", "Unexpected extension publisher.");
  requireCondition(/^\d+\.\d+\.\d+$/.test(manifest.version ?? ""), "Extension version must be stable SemVer.");
  requireCondition(manifest.displayName === "SYNAPSE CHANNEL", "Unexpected extension display name.");
  requireCondition(manifest.license === "AGPL-3.0-or-later", "Unexpected extension licence identifier.");
  requireCondition(manifest.icon === "media/icon.png", "Marketplace icon must be the packaged PNG.");
  requireCondition(manifest.preview === true, "Preview status must remain explicit.");
  requireCondition(manifest.pricing === "Free", "Marketplace pricing label must be explicit.");
  requireCondition(manifest.galleryBanner?.color === "#0c1118", "Marketplace banner colour drifted.");
  requireCondition(manifest.galleryBanner?.theme === "dark", "Marketplace banner theme drifted.");
  requireCondition(manifest.engines?.vscode === "^1.90.0", "VS Code compatibility floor drifted.");
  requireCondition(manifest.main === "./out/extension.js", "Extension entry point drifted.");
  requireCondition(Array.isArray(manifest.extensionKind) && manifest.extensionKind.length === 1 && manifest.extensionKind[0] === "workspace", "Extension must run beside the canonical workspace filesystem.");
  requireCondition(manifest.capabilities?.virtualWorkspaces?.supported === false, "Virtual workspace support must fail closed.");
  requireCondition(manifest.capabilities?.untrustedWorkspaces?.supported === false, "Untrusted workspace support must fail closed.");
  requireCondition(typeof manifest.capabilities?.virtualWorkspaces?.description === "string", "Virtual workspace refusal needs an operator-facing reason.");
  requireCondition(typeof manifest.capabilities?.untrustedWorkspaces?.description === "string", "Untrusted workspace refusal needs an operator-facing reason.");
  requireCondition(Array.isArray(manifest.categories) && manifest.categories.includes("SCM Providers"), "SCM Providers category is required.");
  requireCondition(Array.isArray(manifest.keywords) && manifest.keywords.length > 0 && manifest.keywords.length <= 30, "Marketplace keywords must contain 1–30 values.");
  requireCondition(new Set(manifest.keywords).size === manifest.keywords.length, "Marketplace keywords must be unique.");
  requireCondition(typeof manifest.description === "string" && manifest.description.length >= 40 && manifest.description.length <= 200, "Marketplace description must contain 40–200 characters.");
  requireHttps(manifest.homepage, "Homepage");
  requireHttps(manifest.repository?.url, "Repository URL");
  requireHttps(manifest.bugs?.url, "Issue URL");
}

function imageReferences(markdown) {
  const references = [];
  for (const match of markdown.matchAll(/!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)/g)) {
    references.push(match[1]);
  }
  for (const match of markdown.matchAll(/<img\s+[^>]*src=["']([^"']+)["'][^>]*>/gi)) {
    references.push(match[1]);
  }
  return references;
}

function validateListing(entries) {
  const manifest = parseManifest(entries);
  const readme = entryText(entries, "extension/readme.md");
  const support = entryText(entries, "extension/SUPPORT.md");
  const privacy = entryText(entries, "extension/PRIVACY.md");
  const changelog = entryText(entries, "extension/changelog.md");
  const references = imageReferences(readme);
  requireCondition(references.length >= 3, "Marketplace README must show the icon and two real screenshots.");
  for (const reference of references) {
    requireHttps(reference, `Marketplace image ${reference}`);
    requireCondition(!reference.toLowerCase().endsWith(".svg"), "Marketplace README may not embed SVG images.");
  }
  const imageNames = ["icon.png", "marketplace-board.png", "marketplace-claims.png"];
  const imagePattern = new RegExp(`^https://raw\\.githubusercontent\\.com/anulum/synapse-channel/(${listingCommitPattern})/clients/vscode/media/([^/]+)$`);
  const commits = new Set();
  const liveLinks = [];
  for (const imageName of imageNames) {
    const reference = references.find((candidate) => candidate.endsWith(`/media/${imageName}`));
    requireCondition(reference !== undefined, `Marketplace README must embed ${imageName}.`);
    const match = imagePattern.exec(reference);
    requireCondition(match?.[2] === imageName, `${imageName} must use an immutable repository commit URL.`);
    commits.add(match[1]);
    liveLinks.push(reference);
  }
  for (const phrase of ["privacy notice", "support policy", "security policy", "licence"]) {
    requireCondition(readme.toLowerCase().includes(phrase), `Marketplace README must link its ${phrase}.`);
  }
  for (const path of ["PRIVACY.md", "SUPPORT.md", "LICENSE"]) {
    const pattern = new RegExp(`https://github\\.com/anulum/synapse-channel/blob/(${listingCommitPattern})/clients/vscode/${path.replace(".", "\\.")}`);
    const match = pattern.exec(readme);
    requireCondition(match !== null, `Marketplace README must resolve ${path} at an immutable repository commit.`);
    commits.add(match[1]);
    liveLinks.push(match[0]);
  }
  requireCondition(commits.size === 1, "Marketplace assets and policy links must use one immutable commit.");
  requireCondition(!readme.toLowerCase().includes("not yet published"), "Marketplace README may not contain publication-state wording that becomes stale on acceptance.");
  requireCondition(/Registry\s+availability\s+is\s+established\s+only\s+by\s+that\s+registry's\s+listing\./.test(readme), "Registry availability must remain externally verifiable.");
  requireCondition(readme.includes(`Version ${manifest.version}`), "Marketplace README must identify the packaged version.");
  requireCondition(privacy.includes("no analytics") && privacy.includes("SecretStorage"), "Privacy notice must state telemetry and secret-storage boundaries.");
  requireCondition(support.includes("security policy") && support.includes("issue tracker"), "Support file must route bugs and security reports.");
  requireCondition(changelog.includes(`## ${manifest.version}`), "Change log must include the packaged version.");
  return { liveLinks, listingCommit: [...commits][0] };
}

function validateImages(entries) {
  const icon = readPngDimensions(entries["extension/media/icon.png"], "Marketplace icon");
  requireCondition(icon.width === icon.height && icon.width >= 128, "Marketplace icon must be square and at least 128px.");
  for (const name of ["marketplace-board.png", "marketplace-claims.png"]) {
    const image = readPngDimensions(entries[`extension/media/${name}`], name);
    requireCondition(image.width >= 1200 && image.height >= 700, `${name} must preserve legible real-IDE detail.`);
  }
}

function validatePayload(entries) {
  for (const name of requiredEntries) {
    requireCondition(entries[name] !== undefined, `Missing required VSIX entry: ${name}`);
  }
  const names = Object.keys(entries);
  const forbiddenTree = /^extension\/(?:src|test|node_modules|coverage|dist|out-test|\.vscode-test-cache)(?:\/|$)/;
  requireCondition(!names.some((name) => forbiddenTree.test(name)), "VSIX contains a development-only tree.");
  requireCondition(!names.some((name) => secretFile.test(name)), "VSIX contains a credential-shaped file.");
  for (const name of names) {
    if (binaryEntry.test(name)) {
      continue;
    }
    let content;
    try {
      content = decoder.decode(entries[name]);
    } catch (error) {
      throw new Error(`VSIX contains invalid UTF-8 in text entry ${name}: ${String(error)}`);
    }
    requireCondition(!secretTokenPatterns.some((pattern) => pattern.test(content)), `VSIX contains a high-confidence credential in ${name}.`);
    const assignment = /(?:^|[\s"'`])(?:_authToken|auth[_-]?token|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd)\s*[:=]\s*["']?([^\s"',;}]+)/gim;
    for (const match of content.matchAll(assignment)) {
      const value = match[1].replace(/["']$/, "");
      const placeholder = /^(?:\$\{|process\.env\.|redacted|placeholder|example|change-me|your[-_]|undefined|null|false|await\b)/i;
      requireCondition(value.length < 8 || placeholder.test(value), `VSIX contains a literal credential assignment in ${name}.`);
    }
  }
  requireCondition(names.length <= 40, "VSIX file count exceeded the reviewed package bound.");
}

export function verifyMarketplaceArchive(archive) {
  requireCondition(archive.byteLength <= 2 * 1024 * 1024, "VSIX exceeds the 2 MiB reviewed package bound.");
  let entries;
  try {
    entries = unzipSync(archive);
  } catch (error) {
    throw new Error(`Invalid VSIX ZIP archive: ${String(error)}`);
  }
  validatePayload(entries);
  const manifest = parseManifest(entries);
  validateManifest(manifest);
  requireCondition(entries[`extension/${manifest.main.slice(2)}`] !== undefined, "Manifest entry point is missing from the VSIX.");
  validateImages(entries);
  const listing = validateListing(entries);
  return {
    identity: `${manifest.publisher}.${manifest.name}@${manifest.version}`,
    entries: Object.keys(entries).length,
    bytes: archive.byteLength,
    ...listing,
  };
}

async function requireLiveLinks(links) {
  const checks = await Promise.all(links.map(async (url) => {
    const response = await fetch(url, { method: "HEAD", redirect: "follow" });
    return { ok: response.ok, status: response.status, url };
  }));
  const failed = checks.find((check) => !check.ok);
  requireCondition(failed === undefined, `Registry listing URL is not live (${failed?.status}): ${failed?.url}`);
}

async function main() {
  const args = process.argv.slice(2);
  const requireLive = args.includes("--require-live-links");
  const paths = args.filter((argument) => argument !== "--require-live-links");
  requireCondition(paths.length === 1, "Usage: verify-marketplace-package.mjs [--require-live-links] PACKAGE.vsix");
  const result = verifyMarketplaceArchive(new Uint8Array(await readFile(paths[0])));
  if (requireLive) {
    await requireLiveLinks(result.liveLinks);
  }
  const { liveLinks: _liveLinks, ...summary } = result;
  console.log(`MARKETPLACE_PACKAGE_PASS ${JSON.stringify({ ...summary, liveLinks: requireLive ? "verified" : "deferred" })}`);
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
