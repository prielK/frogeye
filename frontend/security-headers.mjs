// Build-time: emit nginx security headers, including a CSP whose script-src carries the
// sha256 of every inline <script> in the built index.html (the pre-paint theme script).
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

const [, , htmlPath, outPath] = process.argv;
const html = readFileSync(htmlPath, "utf8");
const hashes = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(
  (m) => `'sha256-${createHash("sha256").update(m[1]).digest("base64")}'`,
);
if (hashes.length === 0) throw new Error("no inline script found in " + htmlPath);

const csp = [
  "default-src 'self'",
  `script-src 'self' ${hashes.join(" ")}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

writeFileSync(
  outPath,
  [
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
    'add_header X-Frame-Options "DENY" always;',
    `add_header Content-Security-Policy "${csp}" always;`,
    "",
  ].join("\n"),
);
