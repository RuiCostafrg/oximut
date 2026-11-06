// Preserve source-module resolution when trusted framework modules are bundled.
import { parse } from '@babel/parser';
import MagicString from 'magic-string';
import { pathToFileURL } from 'node:url';

/** Preserve explicit Node ESM URL/resolve bases without replacing source strings. */
export function preserveModuleContext(code, id) {
  const ast = parse(code, { sourceType: 'unambiguous', allowReturnOutsideFunction: true,
    createImportExpressions: true });
  const output = new MagicString(code), origin = JSON.stringify(pathToFileURL(id).href);
  const sites = [];
  /** Recognize actual syntax, rejecting unknown metadata semantics. */
  function visit(node, parent) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'MetaProperty' && node.meta.name === 'import') {
      if (parent?.type !== 'MemberExpression' || parent.object !== node || parent.computed)
        throw Error(`UNSUPPORTED: direct import.meta in ${id}:${node.start}`);
      if (parent.property.name === 'url') output.overwrite(parent.start, parent.end, origin);
      else if (parent.property.name === 'resolve')
        output.overwrite(parent.start, parent.end,
          `((specifier, parent = ${origin}) => import.meta.resolve(specifier, parent))`);
      else throw Error(`UNSUPPORTED: import.meta.${parent.property.name} in ${id}:${node.start}`);
      sites.push({ kind: 'meta:' + parent.property.name, offset: node.start });
    }
    if (node.type === 'ImportExpression' && node.source.type !== 'StringLiteral') {
      // Keep literal imports visible to the bundler. Resolve computed imports
      // from the original file. Resolution/coercion failures must reject the
      // promise; argument expressions still evaluate in the caller as before.
      if (node.phase) throw Error(`UNSUPPORTED: ${node.phase} import in ${id}:${node.start}`);
      output.overwrite(node.start, node.source.start,
        '(async (specifier, options) => import(import.meta.resolve(`${specifier}`, ' + origin + '), options))(');
      sites.push({ kind: 'computed-import', offset: node.start });
    }
    for (const [key, child] of Object.entries(node)) {
      if (['loc', 'start', 'end', 'extra', 'comments', 'tokens'].includes(key)) continue;
      if (Array.isArray(child)) { for (const item of child) visit(item, node); }
      else if (child && typeof child === 'object') visit(child, node);
    }
  }
  visit(ast, null);
  return { code: output.toString(), sites };
}
