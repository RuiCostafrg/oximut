//! AST-based single-edit mutation generation. Inactive instrumentation is identity.
use std::path::Path;

use oxc_allocator::Allocator;
use oxc_ast::ast::*;
use oxc_ast_visit::{Visit, walk};
use oxc_parser::Parser;
use oxc_span::{GetSpan, SourceType, Span};
use oxc_syntax::{operator::BinaryOperator, scope::ScopeFlags};
use oximut_core::{Location, Mutant, Position};
use sha2::{Digest, Sha256};

#[derive(Debug, serde::Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
pub struct Generated {
    pub mutants: Vec<Mutant>,
    pub diagnostics: Vec<String>,
}

struct Edit {
    span: Span,
    expression: Span,
    operator: &'static str,
    replacement: &'static str,
}

struct Collector<'s> {
    source: &'s str,
    edits: Vec<Edit>,
    diagnostics: Vec<String>,
}

impl Collector<'_> {
    fn exclude(&mut self, span: Span, reason: &str) {
        self.diagnostics.push(format!(
            "excluded bytes {}..{}: {reason}",
            span.start, span.end
        ));
    }
}

impl<'a> Visit<'a> for Collector<'_> {
    fn visit_boolean_literal(&mut self, it: &BooleanLiteral) {
        self.edits.push(Edit {
            span: it.span,
            expression: it.span,
            operator: "boolean-literal",
            replacement: if it.value { "false" } else { "true" },
        });
    }

    fn visit_binary_expression(&mut self, it: &BinaryExpression<'a>) {
        use BinaryOperator::*;
        let change = match it.operator {
            LessThan => Some(("<", "<=", "comparison-boundary")),
            LessEqualThan => Some(("<=", "<", "comparison-boundary")),
            GreaterThan => Some((">", ">=", "comparison-boundary")),
            GreaterEqualThan => Some((">=", ">", "comparison-boundary")),
            Addition => Some(("+", "-", "arithmetic")),
            Subtraction => Some(("-", "+", "arithmetic")),
            Multiplication => Some(("*", "/", "arithmetic")),
            Division => Some(("/", "*", "arithmetic")),
            _ => None,
        };
        if let Some((original, replacement, operator)) = change {
            let start = it.left.span().end as usize;
            let end = it.right.span().start as usize;
            // Locate only the AST-identified operator between its two operands.
            // Comments may contain arbitrary operator-looking text.
            if let Some(offset) = operator_offset(&self.source[start..end], original) {
                self.edits.push(Edit {
                    span: Span::new(
                        (start + offset) as u32,
                        (start + offset + original.len()) as u32,
                    ),
                    expression: it.span,
                    operator,
                    replacement,
                });
            } else {
                self.exclude(it.span, "operator token could not be located safely");
            }
        }
        walk::walk_binary_expression(self, it);
    }

    // Literal booleans in type positions are not runtime expressions.
    fn visit_ts_type(&mut self, _: &TSType<'a>) {}

    fn visit_ts_type_alias_declaration(&mut self, _: &TSTypeAliasDeclaration<'a>) {}

    fn visit_ts_interface_declaration(&mut self, _: &TSInterfaceDeclaration<'a>) {}

    fn visit_variable_declaration(&mut self, it: &VariableDeclaration<'a>) {
        if it.declare {
            self.exclude(it.span, "ambient declaration");
        } else {
            walk::walk_variable_declaration(self, it);
        }
    }

    fn visit_function(&mut self, it: &Function<'a>, flags: ScopeFlags) {
        if it.declare || it.body.is_none() {
            self.exclude(it.span, "ambient declaration or overload");
        } else {
            walk::walk_function(self, it, flags);
        }
    }

    fn visit_class(&mut self, it: &Class<'a>) {
        if it.declare {
            self.exclude(it.span, "ambient declaration");
        } else {
            walk::walk_class(self, it);
        }
    }

    fn visit_property_definition(&mut self, it: &PropertyDefinition<'a>) {
        if it.declare || it.r#type == PropertyDefinitionType::TSAbstractPropertyDefinition {
            self.exclude(it.span, "ambient or abstract property");
        } else {
            walk::walk_property_definition(self, it);
        }
    }

    fn visit_ts_enum_declaration(&mut self, it: &TSEnumDeclaration<'a>) {
        self.exclude(it.span, "TypeScript enums are unsupported in M1");
    }

    fn visit_method_definition(&mut self, it: &MethodDefinition<'a>) {
        if it.r#type == MethodDefinitionType::TSAbstractMethodDefinition || it.value.body.is_none()
        {
            self.exclude(it.span, "abstract method or overload");
        } else {
            walk::walk_method_definition(self, it);
        }
    }

    fn visit_accessor_property(&mut self, it: &AccessorProperty<'a>) {
        if it.r#type == AccessorPropertyType::TSAbstractAccessorProperty {
            self.exclude(it.span, "abstract accessor");
        } else {
            walk::walk_accessor_property(self, it);
        }
    }

    fn visit_ts_namespace_declaration(&mut self, it: &TSNamespaceDeclaration<'a>) {
        self.exclude(it.span, "TypeScript namespaces are unsupported in M1");
    }

    fn visit_ts_external_module_declaration(&mut self, it: &TSExternalModuleDeclaration<'a>) {
        self.exclude(it.span, "ambient module");
    }

    fn visit_ts_global_declaration(&mut self, it: &TSGlobalDeclaration<'a>) {
        self.exclude(it.span, "ambient global declaration");
    }

    fn visit_decorator(&mut self, it: &Decorator<'a>) {
        self.exclude(it.span, "decorator expressions are unsupported in M1");
    }
}

fn operator_offset(gap: &str, token: &str) -> Option<usize> {
    let mut offset = 0;
    while offset < gap.len() {
        let rest = &gap[offset..];
        if rest.starts_with("/*") {
            offset += rest.find("*/")? + 2;
        } else if rest.starts_with("//") {
            offset += rest.find(['\n', '\r', '\u{2028}', '\u{2029}'])?;
        } else if let Some(ch) = rest.chars().next().filter(|ch| ch.is_whitespace()) {
            offset += ch.len_utf8();
        } else if let Some(trailing) = rest.strip_prefix(token) {
            // A trailing comment is legal, but additional tokens are not.
            return only_trivia(trailing).then_some(offset);
        } else {
            return None;
        }
    }
    None
}

fn only_trivia(mut source: &str) -> bool {
    loop {
        source = source.trim_start();
        if source.is_empty() {
            return true;
        }
        if source.starts_with("/*") {
            let Some(end) = source.find("*/") else {
                return false;
            };
            source = &source[end + 2..];
        } else if source.starts_with("//") {
            let Some(end) = source.find(['\n', '\r', '\u{2028}', '\u{2029}']) else {
                return true;
            };
            source = &source[end..];
        } else {
            return false;
        }
    }
}

pub fn generate(identity: &str, path: &Path, source: &str) -> Result<Generated, String> {
    if source.len() > u32::MAX as usize {
        return Err("source exceeds Oxc span size".into());
    }
    let source_type = SourceType::from_path(path).map_err(|e| e.to_string())?;
    let allocator = Allocator::default();
    let parsed = Parser::new(&allocator, source, source_type).parse();
    if parsed.fatal_error || !parsed.diagnostics.is_empty() {
        return Err(format!(
            "parse error in {}: {:?}",
            path.display(),
            parsed.diagnostics
        ));
    }
    if source_type.is_typescript_definition() {
        return Ok(Generated {
            mutants: vec![],
            diagnostics: vec!["excluded declaration file".into()],
        });
    }
    let mut collector = Collector {
        source,
        edits: vec![],
        diagnostics: vec![],
    };
    collector.visit_program(&parsed.program);
    collector
        .edits
        .sort_by_key(|edit| (edit.span.start, edit.span.end));
    let file = path.to_string_lossy().into_owned();
    let normalized_identity = identity.replace('\\', "/");
    let mut mutants = vec![];
    for edit in collector.edits {
        let start = edit.span.start as usize;
        let end = edit.span.end as usize;
        let mut hash = Sha256::new();
        for item in [
            &normalized_identity,
            source,
            &start.to_string(),
            &end.to_string(),
            edit.operator,
            edit.replacement,
        ] {
            hash.update((item.len() as u64).to_le_bytes());
            hash.update(item.as_bytes());
        }
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let id = hash
            .finalize()
            .iter()
            .flat_map(|byte| {
                [
                    HEX[(byte >> 4) as usize] as char,
                    HEX[(byte & 15) as usize] as char,
                ]
            })
            .collect::<String>();
        let code = format!("{}{}{}", &source[..start], edit.replacement, &source[end..]);
        // Reparse replacements: grammar-invalid transformations never reach Vitest.
        let transformed = Parser::new(&allocator, &code, source_type).parse();
        if transformed.fatal_error || !transformed.diagnostics.is_empty() {
            collector
                .diagnostics
                .push(format!("excluded {id}: replacement is not valid syntax"));
            continue;
        }
        mutants.push(Mutant {
            id,
            file: file.clone(),
            operator: edit.operator.into(),
            original: source[start..end].into(),
            replacement: edit.replacement.into(),
            location: Location {
                start: position(source, start),
                end: position(source, end),
            },
            map: source_map(&file, source, start, end, edit.replacement),
            code,
        });
    }
    Ok(Generated {
        mutants,
        diagnostics: collector.diagnostics,
    })
}

/// Generate a shared Node-only module containing inline mutation switches.
/// Branches remain in their original lexical context, including await and yield.
/// Files referring to `process` fall back to static mutants because the selector
/// could otherwise resolve to a shadowed binding.
pub fn generate_switched(identity: &str, path: &Path, source: &str) -> Result<Generated, String> {
    let mut generated = generate(identity, path, source)?;
    if generated.mutants.is_empty() {
        return Ok(generated);
    }
    let allocator = Allocator::default();
    let source_type = SourceType::from_path(path).map_err(|e| e.to_string())?;
    let parsed = Parser::new(&allocator, source, source_type).parse();
    struct ProcessUse(bool);
    impl<'a> Visit<'a> for ProcessUse {
        fn visit_identifier_reference(&mut self, it: &IdentifierReference<'a>) {
            self.0 |= it.name == "process";
        }
        fn visit_binding_identifier(&mut self, it: &BindingIdentifier<'a>) {
            self.0 |= it.name == "process";
        }
    }
    let mut process_use = ProcessUse(false);
    process_use.visit_program(&parsed.program);
    if process_use.0 {
        generated.diagnostics.push("mutation switching fallback: process identifier may shadow or modify the Node selector; using fresh static mutants".into());
        return Ok(generated);
    }
    let mut collector = Collector {
        source,
        edits: vec![],
        diagnostics: vec![],
    };
    collector.visit_program(&parsed.program);
    // The M1 generator may reject a replacement; retain exactly its candidates.
    let edits: Vec<_> = collector
        .edits
        .iter()
        .filter_map(|edit| {
            generated
                .mutants
                .iter()
                .find(|m| m.location.start.offset == edit.span.start)
                .map(|m| (edit, m.id.as_str()))
        })
        .collect();
    // Branch duplication is exponential in nesting depth. Bound it before rendering.
    let depth = edits
        .iter()
        .map(|(edit, _)| {
            edits
                .iter()
                .filter(|(outer, _)| {
                    outer.expression.start <= edit.expression.start
                        && outer.expression.end >= edit.expression.end
                })
                .count()
        })
        .max()
        .unwrap_or(0);
    if source
        .len()
        .saturating_add(edits.len().saturating_mul(256))
        .saturating_mul(1usize.checked_shl(depth as u32).unwrap_or(usize::MAX))
        > 8 * 1024 * 1024
    {
        generated.diagnostics.push("mutation switching fallback: nested expression expansion exceeds size budget; using fresh static mutants".into());
        return Ok(generated);
    }
    struct Output {
        code: String,
        origins: Vec<usize>,
    }
    impl Output {
        fn insert(&mut self, text: &str, origin: usize) {
            self.code.push_str(text);
            self.origins.extend(std::iter::repeat_n(origin, text.len()));
        }
        fn original(&mut self, source: &str, start: usize, end: usize) {
            self.code.push_str(&source[start..end]);
            self.origins.extend(start..end);
        }
    }
    fn render(
        source: &str,
        start: usize,
        end: usize,
        edits: &[(&Edit, &str)],
        skip: Option<usize>,
        replacement: Option<&Edit>,
        output: &mut Output,
    ) {
        let mut cursor = start;
        while cursor < end {
            let next = edits
                .iter()
                .enumerate()
                .filter(|(index, (edit, _))| {
                    Some(*index) != skip
                        && edit.expression.start as usize >= cursor
                        && edit.expression.end as usize <= end
                })
                .min_by_key(|(_, (edit, _))| {
                    (
                        edit.expression.start,
                        std::cmp::Reverse(edit.expression.end),
                    )
                });
            let stop = next.map_or(end, |(_, (edit, _))| edit.expression.start as usize);
            if let Some(edit) = replacement.filter(|edit| {
                edit.span.start as usize >= cursor && (edit.span.end as usize) <= stop
            }) {
                output.original(source, cursor, edit.span.start as usize);
                output.insert(edit.replacement, edit.span.start as usize);
                output.original(source, edit.span.end as usize, stop);
            } else {
                output.original(source, cursor, stop);
            }
            let Some((index, (edit, id))) = next else {
                break;
            };
            let a = edit.expression.start as usize;
            let b = edit.expression.end as usize;
            output.insert(
                &format!("(process.env.OXIMUT_ACTIVE_MUTANT === \"{id}\" ? ("),
                a,
            );
            render(source, a, b, edits, Some(index), Some(edit), output);
            output.insert(") : (", a);
            render(source, a, b, edits, Some(index), None, output);
            output.insert("))", b);
            cursor = b;
        }
    }
    let mut output = Output {
        code: String::new(),
        origins: vec![],
    };
    render(source, 0, source.len(), &edits, None, None, &mut output);
    let transformed = Parser::new(&allocator, &output.code, source_type).parse();
    if transformed.fatal_error || !transformed.diagnostics.is_empty() {
        generated.diagnostics.push("mutation switching fallback: shared transform is not valid syntax; using fresh static mutants".into());
        return Ok(generated);
    }
    let map = mapped_code(
        &path.to_string_lossy(),
        source,
        &output.code,
        &output.origins,
    );
    for mutant in &mut generated.mutants {
        mutant.code.clone_from(&output.code);
        mutant.map.clone_from(&map);
    }
    Ok(generated)
}

/// Probe expression evaluation without changing values, operand order, or the
/// lexical context of await/yield. The collector is installed before imports.
pub fn generate_probes(
    identity: &str,
    path: &Path,
    source: &str,
) -> Result<oximut_core::Mutation, String> {
    let generated = generate(identity, path, source)?;
    let allocator = Allocator::default();
    let source_type = SourceType::from_path(path).map_err(|e| e.to_string())?;
    let parsed = Parser::new(&allocator, source, source_type).parse();
    struct GlobalUse(bool);
    impl<'a> Visit<'a> for GlobalUse {
        fn visit_identifier_reference(&mut self, it: &IdentifierReference<'a>) {
            self.0 |= it.name == "globalThis";
        }
        fn visit_binding_identifier(&mut self, it: &BindingIdentifier<'a>) {
            self.0 |= it.name == "globalThis";
        }
    }
    let mut global_use = GlobalUse(false);
    global_use.visit_program(&parsed.program);
    if global_use.0 {
        return Err("probe-globalThis-conflict".into());
    }
    let mut collector = Collector {
        source,
        edits: vec![],
        diagnostics: vec![],
    };
    collector.visit_program(&parsed.program);
    let mut opens = std::collections::BTreeMap::<usize, Vec<(usize, String)>>::new();
    let mut closes = std::collections::BTreeMap::<usize, usize>::new();
    for mutant in &generated.mutants {
        let edit = collector
            .edits
            .iter()
            .find(|edit| edit.span.start == mutant.location.start.offset)
            .ok_or("missing probe expression")?;
        opens
            .entry(edit.expression.start as usize)
            .or_default()
            .push((edit.expression.end as usize, mutant.id.clone()));
        *closes.entry(edit.expression.end as usize).or_default() += 1;
    }
    for entries in opens.values_mut() {
        entries.sort_by_key(|(end, _)| std::cmp::Reverse(*end));
    }
    let mut bytes = Vec::new();
    let mut origins = vec![];
    for offset in 0..=source.len() {
        let mut insert = |text: &str| {
            bytes.extend_from_slice(text.as_bytes());
            origins.extend(std::iter::repeat_n(offset, text.len()));
        };
        if let Some(count) = closes.get(&offset) {
            insert(&"))".repeat(*count));
        }
        if let Some(entries) = opens.get(&offset) {
            for (_, id) in entries {
                insert(&format!("(globalThis.__oximut_m3_hit__?.(\"{id}\"), ("));
            }
        }
        if let Some(byte) = source.as_bytes().get(offset) {
            bytes.push(*byte);
            origins.push(offset);
        }
    }
    let code = String::from_utf8(bytes).map_err(|e| e.to_string())?;
    let transformed = Parser::new(&allocator, &code, source_type).parse();
    if transformed.fatal_error || !transformed.diagnostics.is_empty() {
        return Err("unsupported-probe-syntax".into());
    }
    Ok(oximut_core::Mutation {
        id: "__oximut_probes__".into(),
        file: path.to_string_lossy().into_owned(),
        map: mapped_code(&path.to_string_lossy(), source, &code, &origins),
        code,
    })
}

pub fn position(source: &str, offset: usize) -> Position {
    let mut line = 1;
    let mut column = 1;
    let mut previous_cr = false;
    for ch in source[..offset].chars() {
        if ch == '\n' && previous_cr {
            previous_cr = false;
            continue;
        }
        if matches!(ch, '\n' | '\r' | '\u{2028}' | '\u{2029}') {
            line += 1;
            column = 1;
        } else {
            column += ch.len_utf16() as u32;
        }
        previous_cr = ch == '\r';
    }
    Position {
        offset: offset as u32,
        line,
        column,
    }
}

fn vlq(value: i64, output: &mut String) {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut value = if value < 0 {
        ((-value) as u64) * 2 + 1
    } else {
        value as u64 * 2
    };
    loop {
        let mut digit = (value & 31) as usize;
        value >>= 5;
        if value != 0 {
            digit |= 32;
        }
        output.push(ALPHABET[digit] as char);
        if value == 0 {
            break;
        }
    }
}

/// High-resolution version 3 map. All untouched characters map to their exact
/// original UTF-16 position; replacement characters map to the replaced token.
fn source_map(
    file: &str,
    source: &str,
    start: usize,
    end: usize,
    replacement: &str,
) -> serde_json::Value {
    let code = format!("{}{}{}", &source[..start], replacement, &source[end..]);
    let origins: Vec<_> = (0..code.len())
        .map(|byte| {
            if byte < start {
                byte
            } else if byte < start + replacement.len() {
                start
            } else {
                byte + (end - start) - replacement.len()
            }
        })
        .collect();
    mapped_code(file, source, &code, &origins)
}

fn mapped_code(file: &str, source: &str, code: &str, origins: &[usize]) -> serde_json::Value {
    let mut positions = vec![(0i64, 0i64); source.len() + 1];
    let (mut line, mut column, mut previous_cr) = (0, 0, false);
    for (byte, ch) in source.char_indices() {
        positions[byte] = (line, column);
        if ch == '\n' && previous_cr {
            previous_cr = false;
            continue;
        }
        if matches!(ch, '\n' | '\r' | '\u{2028}' | '\u{2029}') {
            line += 1;
            column = 0;
        } else {
            column += ch.len_utf16() as i64;
        }
        previous_cr = ch == '\r';
    }
    positions[source.len()] = (line, column);
    let mut mappings = String::new();
    let mut generated_column = 0i64;
    let mut previous_generated_column = 0i64;
    let mut previous_line = 0i64;
    let mut previous_column = 0i64;
    let mut first_segment = true;
    let mut prior_cr = false;
    for (byte, ch) in code.char_indices() {
        if ch == '\n' && prior_cr {
            prior_cr = false;
            continue;
        }
        if matches!(ch, '\n' | '\r' | '\u{2028}' | '\u{2029}') {
            mappings.push(';');
            generated_column = 0;
            previous_generated_column = 0;
            first_segment = true;
        } else {
            let original_byte = origins[byte];
            let (original_line, original_column) = positions[original_byte];
            if !first_segment {
                mappings.push(',');
            }
            first_segment = false;
            vlq(generated_column - previous_generated_column, &mut mappings);
            vlq(0, &mut mappings);
            vlq(original_line - previous_line, &mut mappings);
            vlq(original_column - previous_column, &mut mappings);
            previous_generated_column = generated_column;
            previous_line = original_line;
            previous_column = original_column;
            generated_column += ch.len_utf16() as i64;
        }
        prior_cr = ch == '\r';
    }
    serde_json::json!({"version":3,"file":file,"sources":[file],"sourcesContent":[source],"names":[],"mappings":mappings})
}

#[cfg(test)]
mod tests {
    use super::*;
    fn mutations(source: &str) -> Generated {
        generate("src/a.ts", Path::new("/project/src/a.ts"), source).unwrap()
    }

    #[test]
    fn produces_supported_operators_only_and_preserves_comments() {
        let source = "const a = true; const b = x /* < true */ < /* false */ y; const c = 1+2-3*4/5; // false";
        let result = mutations(source);
        assert_eq!(result.mutants.len(), 6);
        assert_eq!(result.mutants[0].code, source.replacen("true", "false", 1));
        assert!(
            result.mutants[1]
                .code
                .contains("/* < true */ <= /* false */")
        );
        assert_eq!(
            mutations("const a = 1 === 2; const b = 3 % 2;")
                .mutants
                .len(),
            0
        );
    }

    #[test]
    fn excludes_types_ambient_enum_namespace_and_decorators() {
        let result = mutations(
            "type A = true; interface B { x: false }; declare const c: true; enum E { X = 1 + 2 }; namespace N { const t = false }; const real: true = true;",
        );
        assert_eq!(result.mutants.len(), 1);
        assert!(
            result.mutants[0]
                .code
                .ends_with("const real: true = false;")
        );
        assert!(result.diagnostics.iter().any(|d| d.contains("enum")));
        assert!(
            generate("a.d.ts", Path::new("a.d.ts"), "declare const x: true;")
                .unwrap()
                .mutants
                .is_empty()
        );
    }

    #[test]
    fn excludes_computed_keys_in_erased_types_and_abstract_members() {
        let result = mutations(
            "interface A { [x + y]: true }; abstract class B { abstract [x + y](): true; abstract [x - y]: false; }; const runtime = false;",
        );
        assert_eq!(result.mutants.len(), 1);
        assert!(result.mutants[0].code.ends_with("const runtime = true;"));
    }

    #[test]
    fn identities_are_stable_and_source_identity_and_content_sensitive() {
        let first = mutations("const x = true;");
        assert_eq!(
            first.mutants[0].id,
            mutations("const x = true;").mutants[0].id
        );
        assert_ne!(
            first.mutants[0].id,
            mutations("const x = true; ").mutants[0].id
        );
        assert_ne!(
            first.mutants[0].id,
            generate("other.ts", Path::new("a.ts"), "const x = true;")
                .unwrap()
                .mutants[0]
                .id
        );
    }

    #[test]
    fn unicode_locations_and_source_maps_use_utf16_columns() {
        let source = "// 😀\r\nconst s = '😀'; const x = true;\n";
        let mutant = &mutations(source).mutants[0];
        assert_eq!(mutant.location.start.line, 2);
        assert_eq!(mutant.location.start.column, 27);
        assert_eq!(
            mutant.location.start.offset as usize,
            source.find("true").unwrap()
        );
        assert_eq!(mutant.map["version"], 3);
        assert_eq!(mutant.map["sourcesContent"][0], source);
        assert_eq!(
            mutant.map["mappings"]
                .as_str()
                .unwrap()
                .matches(';')
                .count(),
            2
        );
    }

    #[test]
    fn rejects_parse_errors_without_generating_mutants() {
        assert!(
            generate("a.ts", Path::new("a.ts"), "const = true")
                .unwrap_err()
                .contains("parse error")
        );
    }
    fn execute(code: &str, active: &str) -> String {
        let result = std::process::Command::new("node")
            .args(["--input-type=module", "--eval", code])
            .env("OXIMUT_ACTIVE_MUTANT", active)
            .output()
            .expect("Node is required for switching semantics tests");
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        String::from_utf8(result.stdout).unwrap()
    }

    #[test]
    fn switching_matches_static_mutants_with_nested_side_effects_await_and_yield() {
        let source = r#"
let calls = [];
const value = n => { calls.push(n); return n; };
const object = n => ({ valueOf() { calls.push(`coerce${n}`); return n; } });
async function run() {
  const a = (await Promise.resolve(value(8))) + value(3) * value(2);
  const b = object(4) + object(2) / object(1);
  function* sequence() { return (yield value(9)) - value(2); }
  const iterator = sequence();
  const first = iterator.next();
  const last = iterator.next(7);
  console.log(JSON.stringify([a,b,first,last,true, value(2) < value(3), calls]));
}
await run();
"#;
        let fresh = mutations(source);
        let shared = generate_switched("src/a.ts", Path::new("/project/src/a.ts"), source).unwrap();
        assert_eq!(fresh.mutants.len(), shared.mutants.len());
        assert_eq!(execute(source, ""), execute(&shared.mutants[0].code, ""));
        for (static_mutant, switched) in fresh.mutants.iter().zip(&shared.mutants) {
            assert_eq!(static_mutant.id, switched.id);
            assert_eq!(
                serde_json::to_value(&static_mutant.location).unwrap(),
                serde_json::to_value(&switched.location).unwrap()
            );
            assert_eq!(switched.code, shared.mutants[0].code);
            assert_eq!(switched.map, shared.mutants[0].map);
            assert_eq!(
                execute(&static_mutant.code, ""),
                execute(&switched.code, &switched.id)
            );
        }
        assert_eq!(shared.mutants[0].map["sourcesContent"][0], source);
    }

    #[test]
    fn switching_falls_back_for_shadowed_process_and_large_expansion() {
        let source = "function f(process) { return true; }";
        let result = generate_switched("a.ts", Path::new("a.ts"), source).unwrap();
        assert_eq!(result.mutants[0].code, source.replace("true", "false"));
        assert!(
            result
                .diagnostics
                .iter()
                .any(|d| d.contains("switching fallback"))
        );
        let source = format!(
            "const n = {};",
            std::iter::repeat_n("1", 25).collect::<Vec<_>>().join(" + ")
        );
        let result = generate_switched("a.ts", Path::new("a.ts"), &source).unwrap();
        assert_eq!(result.mutants.len(), 24);
        assert!(result.diagnostics.iter().any(|d| d.contains("size budget")));
    }
    #[test]
    fn probes_preserve_nested_evaluation_coercion_short_circuit_await_and_yield() {
        let source = r#"
let calls = [];
const value = n => { calls.push(n); return n; };
const object = n => ({ valueOf() { calls.push(`coerce${n}`); return n; } });
const a = (await Promise.resolve(value(8))) + value(3) * value(2);
const b = object(4) + object(2) / object(1);
const c = false && value(99) < value(100);
function* sequence() { return (yield value(9)) - value(2); }
const iterator = sequence();
console.log(JSON.stringify([a, b, c, iterator.next(), iterator.next(7), calls]));
"#;
        let probe = generate_probes("src/a.ts", Path::new("/project/src/a.ts"), source).unwrap();
        let original = execute(source, "");
        assert_eq!(execute(&probe.code, ""), original);
        let instrumented = format!(
            "globalThis.__oximut_m3_hit__ = id => {{ if (id.length !== 64) throw Error('bad id'); }};{}",
            probe.code
        );
        assert_eq!(execute(&instrumented, ""), original);
        for mutant in mutations(source).mutants {
            assert_eq!(probe.code.matches(&mutant.id).count(), 1);
        }
        assert_eq!(probe.map["sourcesContent"][0], source);
    }

    #[test]
    fn probes_refuse_shadowed_or_referenced_global_this() {
        for source in [
            "function f(globalThis) { return true; }",
            "globalThis.x = true;",
        ] {
            assert_eq!(
                generate_probes("a.ts", Path::new("a.ts"), source).unwrap_err(),
                "probe-globalThis-conflict"
            );
        }
    }
}
