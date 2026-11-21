// Read-only interoperability check against a supplied CUT checkout.
// Exposes readPackage only in memory; never invokes admission or a Provider.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [cutRoot, handoffRoot, taskId] = process.argv.slice(2);
if (!cutRoot || !handoffRoot || !taskId) {
  throw new Error("Usage: node verify_cut_product_package.mjs CUT_ROOT HANDOFF_ROOT TASK_ID");
}
// verifyProductImage resolves Next/sharp from the supplied CUT installation.
process.chdir(path.resolve(cutRoot));
const modulePath = path.join(process.cwd(), "lib/classic-multisource.mjs");
const original = await readFile(modulePath, "utf8");
const exported = original.replace(/from (["'])(\.[^"']+)\1/g, (_match, _quote, relative) =>
  `from ${JSON.stringify(pathToFileURL(path.resolve(path.dirname(modulePath), relative)).href)}`
) + "\nexport { readPackage };\n";
const { readPackage } = await import(`data:text/javascript;base64,${Buffer.from(exported).toString("base64")}`);
const root = path.resolve(handoffRoot);
const relative = `inbox/multi-${taskId}/manifest.json`;
const raw = await readFile(path.join(root, relative));
const manifest = JSON.parse(raw);
const digest = createHash("sha256").update(raw).digest("hex");
const { manifest: verified } = await readPackage({ nasRoot: root }, {
  contractVersion: manifest.contractVersion, taskId, handoffKey: `multi-${taskId}`,
  idempotencyKey: manifest.idempotencyKey, manifestRelativePath: relative, manifestSha256: digest,
});
const scannerRoot = path.join(root, path.dirname(relative), manifest.nativeIntake.relativePath);
const videos = [], images = [];
async function walk(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) await walk(absolute);
    else if (entry.isFile()) {
      const relativePath = path.relative(scannerRoot, absolute).split(path.sep).join("/");
      const item = { name: entry.name, relativePath };
      if (/\.mp4$/i.test(entry.name)) videos.push(item);
      else if (/\.(jpg|jpeg|png|webp)$/i.test(entry.name)) images.push(item);
    }
  }
}
await walk(scannerRoot);
const { groupProductsByProductDirectory } = await import(
  pathToFileURL(path.join(process.cwd(), "worker/filename-product-grouper.mjs"))
);
const detection = groupProductsByProductDirectory(videos, images);
assert.equal(detection.isCompliant, true, JSON.stringify(detection.reasons));
assert.equal(detection.autoApproved, true);
assert.equal(detection.unassigned.length, 0);
assert.equal(detection.groups.length, manifest.nativeIntake.groups.length);
assert.equal(videos.length, manifest.sources.length);
for (const group of manifest.nativeIntake.groups) {
  for (const item of [...group.videos, ...group.productImages]) {
    const file = path.join(root, path.dirname(relative), item.relativePath);
    const bytes = await readFile(file);
    assert.equal(bytes.length, item.size);
    assert.equal(createHash("sha256").update(bytes).digest("hex"), item.sha256);
  }
}
console.log(JSON.stringify({
  validatorSha256: createHash("sha256").update(original).digest("hex"),
  manifestSha256: digest, sourceCount: verified.sources.length,
  verifiedReferences: verified.productReferences.length,
  nativeGroups: detection.groups.length, nativeVideos: videos.length, nativeImages: images.length,
  autoApproved: detection.autoApproved, providerCalls: 0, admissions: 0,
}, null, 2));
