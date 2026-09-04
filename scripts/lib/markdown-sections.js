/*
 * scripts/lib/markdown-sections.js — the `##` heading parser shared by
 * scripts/check-guidance-coverage.js (_agent-guidance#119) and
 * scripts/check-guidance-touch.js (_agent-guidance#120).
 *
 * REAL MARKDOWN PARSE (markdown-it), never a regex or line scanner. base.md
 * contains fenced code blocks, and a `## ` inside one is not a heading — a
 * line scanner cannot tell the difference; a real parser's token stream
 * already has.
 *
 * Level-2 headings only: a `###` subsection is folded into its parent's
 * extent, not extracted as its own heading — matching the manifest's own
 * rule that `###` is never a row.
 */
const MarkdownIt = require("markdown-it");

const md = new MarkdownIt();

// extractHeadings(src, file) — every `##` heading in `src`, in document
// order, with its byte extent (from the heading through the line before the
// next `##` heading, or end of file) and the 0-indexed line range
// [startLine, endLine) that produces it — `startLine` is the heading's own
// line, `endLine` is the exclusive end of its extent (the next heading's
// `startLine`, or the file's line count). check-guidance-coverage.js uses
// `heading`/`file`/`bytes`; check-guidance-touch.js additionally slices
// `src.split("\n")` by `startLine`/`endLine` to compare a section's body text
// across two commits.
function extractHeadings(src, file) {
  const lines = src.split("\n");
  const totalBytes = Buffer.byteLength(src, "utf8");
  // Byte offset of the START of each real line, in UTF-8. `lines` always has
  // exactly one MORE element than the file has newlines — a file ending in
  // "\n" (every file here does) splits to a trailing "" phantom entry that is
  // not a real line and must not be charged a newline of its own, or the last
  // heading in every file overcounts by exactly one byte (measured: it did,
  // until this loop stopped one short of `lines.length`).
  const lineStart = [0];
  for (let i = 0; i < lines.length - 1; i++) {
    lineStart.push(lineStart[lineStart.length - 1] + Buffer.byteLength(lines[i], "utf8") + 1);
  }
  const offsetAt = (lineIdx) => (lineIdx < lines.length ? lineStart[lineIdx] : totalBytes);

  const tokens = md.parse(src, {});
  const raw = [];
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.type === "heading_open" && t.tag === "h2") {
      const startLine = t.map[0];
      // The inline token immediately after heading_open carries the parsed
      // text (closing `##` stripped, leading indentation stripped) — a
      // regex over the raw source line gets both wrong: `## Closed Form ##`
      // keeps its trailing hashes, and a leading-whitespace ATX heading
      // (CommonMark allows up to 3 spaces) does not match `^##\s+` at all.
      const inline = tokens[i + 1];
      raw.push({ text: inline.content, startLine });
    }
  }

  return raw.map((h, i) => {
    const endLine = i + 1 < raw.length ? raw[i + 1].startLine : lines.length;
    return {
      heading: h.text,
      file,
      startLine: h.startLine,
      endLine,
      bytes: offsetAt(endLine) - offsetAt(h.startLine),
    };
  });
}

module.exports = { extractHeadings };
