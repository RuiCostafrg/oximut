import { dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { RolldownMagicString } from 'rolldown';
import { parseAst } from 'rolldown/parseAst';

export function preserveMetadata(source, id, snapshot = false) {
  const origin = JSON.stringify(pathToFileURL(id).href);
  const edits = new RolldownMagicString(source);
  function visit(node, parent) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'MemberExpression' && node.object.type === 'MetaProperty'
        && node.object.meta.name === 'import' && node.object.property.name === 'meta') {
      const key = node.computed ? node.property.value : node.property.name;
      const metadata = {url: origin, dirname: JSON.stringify(dirname(id)), filename: JSON.stringify(id)};
      if (key in metadata) edits.overwrite(node.start, node.end, metadata[key]);
      else if (key === 'resolve') {
        const resolver = snapshot ? 'globalThis.__oximutSnapshotResolve' : 'import.meta.resolve';
        if (parent?.callee === node)
          edits.overwrite(node.start, node.end, `((id, parent = ${origin}) => ${resolver}(id, parent))`);
        else if (snapshot) edits.overwrite(node.start, node.end, resolver);
      }
    }
    if (Array.isArray(node)) {
      for (const child of node) visit(child, parent);
    } else {
      for (const child of Object.values(node)) visit(child, node);
    }
  }
  visit(parseAst(source, null, id));
  return {code: edits.toString(), map: edits.generateMap({hires: true}).toString()};
}
