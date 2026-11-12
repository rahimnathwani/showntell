// The browser entry vendor.sh bundles from the reladraw package: draw() as the package's own,
// except that each node and line is wrapped in a group naming it (data-node, data-edge), which
// is what cues point at and step reveals show.
import { fileChecks, findings } from "./package/dist/checks.js";
import { SourceError } from "./package/dist/errors.js";
import { splitLines } from "./package/dist/measure.js";
import { blame, parse } from "./package/dist/parser.js";
import { renderTagged } from "./package/dist/render.js";
import { resolve } from "./package/dist/resolve.js";

export { splitLines };

export function draw(source, options = {}) {
  const run = (text) => {
    const layout = resolve(parse(text), options);
    const { tagged } = renderTagged(layout, options);
    return { svg: tagged, findings: findings(layout, fileChecks(layout)) };
  };
  try {
    return run(source);
  } catch (error) {
    throw error instanceof SourceError ? blame(source, error, run) : error;
  }
}
