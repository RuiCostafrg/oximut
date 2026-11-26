//! AST-based single-edit mutation generation. Inactive instrumentation is identity.
use std::{collections::BTreeSet, path::Path};

use oxc_allocator::Allocator;
use oxc_ast::ast::*;
use oxc_ast_visit::{Visit, walk};
use oxc_parser::Parser;
use oxc_span::{GetSpan, SourceType, Span};
use oxc_syntax::{
    operator::{
        AssignmentOperator, BinaryOperator, LogicalOperator, UnaryOperator, UpdateOperator,
    },
    scope::ScopeFlags,
};
use oximut_core::{Location, Mutant, Position};
use sha2::{Digest, Sha256};

#[derive(Debug, serde::Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
pub struct Generated {
    pub mutants: Vec<Mutant>,
    pub diagnostics: Vec<String>,
}

/// The mutator families a run selects; empty selects every family.
pub type Families = BTreeSet<String>;

pub const ARITHMETIC: &str = "arithmetic";
pub const ASSIGNMENT: &str = "assignment-operator";
pub const BOOLEAN: &str = "boolean-literal";
pub const COMPARISON: &str = "comparison-boundary";
pub const EQUALITY: &str = "equality-operator";
pub const LOGICAL: &str = "logical-operator";
pub const UNARY: &str = "unary-operator";
pub const UPDATE: &str = "update-operator";

/// One token-level rewrite: the family it belongs to, the token the AST says is
/// there, and its replacement. An operator may carry several rules (`<` has a
/// boundary and an equality rewrite); each becomes its own mutant.
///
/// Adding a family means adding rules here (and its name to
/// `oximut_core::MUTATOR_FAMILIES`) plus, for a new node type, one `visit_*`
/// method that calls `Collector::token`.
type Rule = (&'static str, &'static str, &'static str);

fn binary_rules(operator: BinaryOperator) -> &'static [Rule] {
    use BinaryOperator::*;
    match operator {
        LessThan => &[(COMPARISON, "<", "<="), (EQUALITY, "<", ">=")],
        LessEqualThan => &[(COMPARISON, "<=", "<"), (EQUALITY, "<=", ">")],
        GreaterThan => &[(COMPARISON, ">", ">="), (EQUALITY, ">", "<=")],
        GreaterEqualThan => &[(COMPARISON, ">=", ">"), (EQUALITY, ">=", "<")],
        StrictEquality => &[(EQUALITY, "===", "!==")],
        StrictInequality => &[(EQUALITY, "!==", "===")],
        Equality => &[(EQUALITY, "==", "!=")],
        Inequality => &[(EQUALITY, "!=", "==")],
        Addition => &[(ARITHMETIC, "+", "-")],
        Subtraction => &[(ARITHMETIC, "-", "+")],
        Multiplication => &[(ARITHMETIC, "*", "/")],
        Division => &[(ARITHMETIC, "/", "*")],
        _ => &[],
    }
}

fn logical_rules(operator: LogicalOperator) -> &'static [Rule] {
    match operator {
        LogicalOperator::And => &[(LOGICAL, "&&", "||")],
        LogicalOperator::Or => &[(LOGICAL, "||", "&&")],
        LogicalOperator::Coalesce => &[(LOGICAL, "??", "&&")],
    }
}

fn unary_rules(operator: UnaryOperator) -> &'static [Rule] {
    match operator {
        UnaryOperator::UnaryPlus => &[(UNARY, "+", "-")],
        UnaryOperator::UnaryNegation => &[(UNARY, "-", "+")],
        UnaryOperator::BitwiseNot => &[(UNARY, "~", "")],
        // Negation removal is StrykerJS's BooleanLiteral mutation of `!x`; it is
        // grouped here, with the other prefix operators, so `boolean-literal`
        // keeps generating exactly what it always did.
        UnaryOperator::LogicalNot => &[(UNARY, "!", "")],
        _ => &[],
    }
}

fn update_rules(operator: UpdateOperator) -> &'static [Rule] {
    match operator {
        UpdateOperator::Increment => &[(UPDATE, "++", "--")],
        UpdateOperator::Decrement => &[(UPDATE, "--", "++")],
    }
}

fn assignment_rules(operator: AssignmentOperator) -> &'static [Rule] {
    use AssignmentOperator::*;
    match operator {
        Addition => &[(ASSIGNMENT, "+=", "-=")],
        Subtraction => &[(ASSIGNMENT, "-=", "+=")],
        Multiplication => &[(ASSIGNMENT, "*=", "/=")],
        Division => &[(ASSIGNMENT, "/=", "*=")],
        Remainder => &[(ASSIGNMENT, "%=", "*=")],
        ShiftLeft => &[(ASSIGNMENT, "<<=", ">>=")],
        ShiftRight => &[(ASSIGNMENT, ">>=", "<<=")],
        BitwiseAnd => &[(ASSIGNMENT, "&=", "|=")],
        BitwiseOR => &[(ASSIGNMENT, "|=", "&=")],
        LogicalAnd => &[(ASSIGNMENT, "&&=", "||=")],
        LogicalOr => &[(ASSIGNMENT, "||=", "&&=")],
        LogicalNullish => &[(ASSIGNMENT, "??=", "&&=")],
        _ => &[],
    }
}

struct Edit {
    span: Span,
    expression: Span,
    operator: &'static str,
    replacement: &'static str,
    load_time: bool,
    /// Spans the static (single-mutant) code parenthesizes so the replaced
    /// token swaps one operator and nothing else: `a && b && c` with its first
    /// `&&` turned into `||` is `(a || b) && c`, not `a || b && c`. Switched
    /// code already wraps every edited expression, so it needs none.
    wrap: Vec<Span>,
}

/// Where a logical expression sits among other logical expressions, which is
/// all that decides whether swapping its operator regroups its neighbours.
struct Grouping {
    /// The operator of an unparenthesized logical expression this one is a direct operand of.
    parent: Option<LogicalOperator>,
    /// Unparenthesized logical operands of this expression.
    operands: Vec<(Span, LogicalOperator)>,
}

/// `&&` binds tighter than `||` and `??`; the latter two do not mix with the
/// former or each other without parentheses.
fn logical_precedence(operator: LogicalOperator) -> u8 {
    match operator {
        LogicalOperator::And => 2,
        LogicalOperator::Or | LogicalOperator::Coalesce => 1,
    }
}

impl Grouping {
    fn wraps(&self, expression: Span, replacement: &str) -> Vec<Span> {
        let replaced = if replacement == "&&" { 2 } else { 1 };
        let mut spans: Vec<Span> = self
            .operands
            .iter()
            .filter(|(_, operator)| logical_precedence(*operator) < replaced)
            .map(|(span, _)| *span)
            .collect();
        if let Some(parent) = self.parent
            && (replaced < logical_precedence(parent) || parent == LogicalOperator::Coalesce)
        {
            spans.push(expression);
        }
        spans
    }
}

impl Edit {
    /// The edit a generated mutant came from. A mutant id hashes exactly these
    /// four fields, so they are unique per edit (two edits may share a span).
    fn produced(&self, mutant: &Mutant) -> bool {
        self.span.start == mutant.location.start.offset
            && self.span.end == mutant.location.end.offset
            && self.operator == mutant.operator
            && self.replacement == mutant.replacement
    }
}

struct Collector<'s> {
    source: &'s str,
    families: &'s Families,
    edits: Vec<Edit>,
    diagnostics: Vec<String>,
    /// Function-like bodies currently being walked that run only when called.
    deferred: usize,
    /// Spans of function expressions and arrows whose creation is the whole
    /// effect of the surrounding construct (`const f = () => ..`): their bodies
    /// run when called, not when the module loads.
    deferred_bodies: std::collections::HashSet<(u32, u32)>,
    /// Enclosing logical expressions: operator and the spans of both operands.
    logical_frames: Vec<(LogicalOperator, Span, Span)>,
}

/// Look through parentheses and TypeScript-only wrappers to the expression that
/// decides what a value is at run time.
fn unwrapped<'e, 'a>(mut expression: &'e Expression<'a>) -> &'e Expression<'a> {
    loop {
        expression = match expression {
            Expression::ParenthesizedExpression(inner) => &inner.expression,
            Expression::TSAsExpression(inner) => &inner.expression,
            Expression::TSSatisfiesExpression(inner) => &inner.expression,
            Expression::TSNonNullExpression(inner) => &inner.expression,
            Expression::TSTypeAssertion(inner) => &inner.expression,
            _ => return expression,
        };
    }
}

fn function_span(expression: &Expression<'_>) -> Option<(u32, u32)> {
    match unwrapped(expression) {
        Expression::FunctionExpression(function) => Some((function.span.start, function.span.end)),
        Expression::ArrowFunctionExpression(arrow) => Some((arrow.span.start, arrow.span.end)),
        _ => None,
    }
}

impl Collector<'_> {
    /// Record that this value, when it is a function expression or arrow, is
    /// only created here and called later.
    fn defer_body(&mut self, value: &Expression<'_>) {
        if let Some(span) = function_span(value) {
            self.deferred_bodies.insert(span);
        }
    }

    fn exclude(&mut self, span: Span, reason: &str) {
        self.diagnostics.push(format!(
            "excluded bytes {}..{}: {reason}",
            span.start, span.end
        ));
    }

    fn selects(&self, family: &str) -> bool {
        self.families.is_empty() || self.families.contains(family)
    }

    /// Apply every selected rule whose token the AST says sits in `gap` (the
    /// source between two operands, or between an operand and the end of its
    /// expression). Only that token is located, never text inside comments.
    fn token(
        &mut self,
        rules: &[Rule],
        gap: (u32, u32),
        expression: Span,
        grouping: Option<&Grouping>,
    ) {
        for &(family, original, replacement) in rules {
            if !self.selects(family) {
                continue;
            }
            let (start, end) = (gap.0 as usize, gap.1 as usize);
            let Some(offset) = self.source.get(start..end).and_then(|between| {
                // An assignment or update target's span stops inside its
                // own parentheses; every other operand's includes them.
                operator_offset(between, original, matches!(family, ASSIGNMENT | UPDATE))
            }) else {
                self.exclude(expression, "operator token could not be located safely");
                continue;
            };
            let at = start + offset;
            // Binary-position replacements always have an operand before them.
            // Prefix tokens do not, so they must not fuse with what precedes.
            if matches!(family, UNARY | UPDATE) && !token_is_isolated(self.source, at, replacement)
            {
                self.exclude(
                    expression,
                    "replacement would fuse with the preceding token",
                );
                continue;
            }
            self.edits.push(Edit {
                span: Span::new(at as u32, (at + original.len()) as u32),
                expression,
                operator: family,
                replacement,
                load_time: self.deferred == 0,
                wrap: grouping.map_or(vec![], |grouping| grouping.wraps(expression, replacement)),
            });
        }
    }
}

/// Whether swapping the token at `at` for `replacement` keeps its neighbours as
/// separate tokens: `a-+b` must not become `a--b`, and removing the `!` of
/// `typeof!x` must not glue `typeof` to `x`.
fn token_is_isolated(source: &str, at: usize, replacement: &str) -> bool {
    let Some(previous) = source[..at].chars().next_back() else {
        return true;
    };
    match replacement.chars().next() {
        Some(first @ ('+' | '-')) => previous != first,
        Some(_) => true,
        None => !(previous.is_alphanumeric() || matches!(previous, '_' | '$')),
    }
}

impl<'a> Visit<'a> for Collector<'_> {
    fn visit_boolean_literal(&mut self, it: &BooleanLiteral) {
        if !self.selects(BOOLEAN) {
            return;
        }
        self.edits.push(Edit {
            span: it.span,
            expression: it.span,
            operator: BOOLEAN,
            replacement: if it.value { "false" } else { "true" },
            load_time: self.deferred == 0,
            wrap: vec![],
        });
    }

    fn visit_binary_expression(&mut self, it: &BinaryExpression<'a>) {
        let gap = (it.left.span().end, it.right.span().start);
        self.token(binary_rules(it.operator), gap, it.span, None);
        walk::walk_binary_expression(self, it);
    }

    fn visit_logical_expression(&mut self, it: &LogicalExpression<'a>) {
        let gap = (it.left.span().end, it.right.span().start);
        let direct =
            |frame: &&(LogicalOperator, Span, Span)| it.span == frame.1 || it.span == frame.2;
        let grouping = Grouping {
            parent: self
                .logical_frames
                .last()
                .filter(direct)
                .map(|frame| frame.0),
            operands: [&it.left, &it.right]
                .into_iter()
                .filter_map(|operand| match operand {
                    Expression::LogicalExpression(inner) => Some((inner.span, inner.operator)),
                    _ => None,
                })
                .collect(),
        };
        self.token(logical_rules(it.operator), gap, it.span, Some(&grouping));
        self.logical_frames
            .push((it.operator, it.left.span(), it.right.span()));
        walk::walk_logical_expression(self, it);
        self.logical_frames.pop();
    }

    fn visit_unary_expression(&mut self, it: &UnaryExpression<'a>) {
        let gap = (it.span.start, it.argument.span().start);
        self.token(unary_rules(it.operator), gap, it.span, None);
        walk::walk_unary_expression(self, it);
    }

    fn visit_update_expression(&mut self, it: &UpdateExpression<'a>) {
        // `++x` has its token before the operand, `x++` after it.
        let gap = if it.prefix {
            (it.span.start, it.argument.span().start)
        } else {
            (it.argument.span().end, it.span.end)
        };
        self.token(update_rules(it.operator), gap, it.span, None);
        walk::walk_update_expression(self, it);
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
            let deferred =
                it.is_declaration() || self.deferred_bodies.contains(&(it.span.start, it.span.end));
            self.deferred += usize::from(deferred);
            walk::walk_function(self, it, flags);
            self.deferred -= usize::from(deferred);
        }
    }

    fn visit_arrow_function_expression(&mut self, it: &ArrowFunctionExpression<'a>) {
        let deferred = self.deferred_bodies.contains(&(it.span.start, it.span.end));
        self.deferred += usize::from(deferred);
        walk::walk_arrow_function_expression(self, it);
        self.deferred -= usize::from(deferred);
    }

    // Anything not listed here (call arguments, array elements, operands, an
    // immediately invoked function) may run its function body while the module
    // loads, so its mutants count as load time.
    fn visit_variable_declarator(&mut self, it: &VariableDeclarator<'a>) {
        if let Some(init) = &it.init {
            self.defer_body(init);
        }
        walk::walk_variable_declarator(self, it);
    }

    fn visit_assignment_expression(&mut self, it: &AssignmentExpression<'a>) {
        let gap = (it.left.span().end, it.right.span().start);
        self.token(assignment_rules(it.operator), gap, it.span, None);
        self.defer_body(&it.right);
        walk::walk_assignment_expression(self, it);
    }

    fn visit_object_property(&mut self, it: &ObjectProperty<'a>) {
        self.defer_body(&it.value);
        walk::walk_object_property(self, it);
    }

    fn visit_export_default_declaration(&mut self, it: &ExportDefaultDeclaration<'a>) {
        if let Some(expression) = it.declaration.as_expression() {
            self.defer_body(expression);
        }
        walk::walk_export_default_declaration(self, it);
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
            // A field initializer runs when the class is defined or constructed:
            // load time. A function value's body still runs only when called.
            if let Some(value) = &it.value {
                self.defer_body(value);
            }
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
            // Methods, accessors and constructors run when called or constructed.
            self.deferred_bodies
                .insert((it.value.span.start, it.value.span.end));
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

/// Offset of `token` in the source between two operands, skipping comments and
/// whitespace and, when `parens`, parentheses wrapping an operand whose span
/// does not include them. Anything else before the token means it is not the
/// AST's operator, and nothing but trivia may follow it.
fn operator_offset(gap: &str, token: &str, parens: bool) -> Option<usize> {
    let mut offset = 0;
    while offset < gap.len() {
        let rest = &gap[offset..];
        if rest.starts_with("/*") {
            offset += rest.find("*/")? + 2;
        } else if rest.starts_with("//") {
            offset += rest.find(['\n', '\r', '\u{2028}', '\u{2029}'])?;
        } else if let Some(ch) = rest.chars().next().filter(|ch| ch.is_whitespace()) {
            offset += ch.len_utf8();
        } else if parens && rest.starts_with(')') {
            offset += 1;
        } else if let Some(trailing) = rest.strip_prefix(token) {
            // A trailing comment is legal, but additional tokens are not.
            return only_trivia(trailing, parens).then_some(offset);
        } else {
            return None;
        }
    }
    None
}

fn only_trivia(mut source: &str, parens: bool) -> bool {
    loop {
        source = source.trim_start();
        if parens && let Some(rest) = source.strip_prefix(['(', ')']) {
            source = rest;
            continue;
        }
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

/// Every mutant of `source` in the selected families (empty selects all).
pub fn generate(
    identity: &str,
    path: &Path,
    source: &str,
    families: &Families,
) -> Result<Generated, String> {
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
        families,
        edits: vec![],
        diagnostics: vec![],
        deferred: 0,
        deferred_bodies: Default::default(),
        logical_frames: vec![],
    };
    collector.visit_program(&parsed.program);
    collector
        .edits
        .sort_by_key(|edit| (edit.span.start, edit.span.end));
    let file = path.to_string_lossy().into_owned();
    let normalized_identity = identity.replace('\\', "/");
    let mut mutants = vec![];
    let mut distinct = BTreeSet::new();
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
        let (code, origins) = edited_code(source, &edit);
        // Reparse replacements: grammar-invalid transformations never reach Vitest.
        let transformed = Parser::new(&allocator, &code, source_type).parse();
        if transformed.fatal_error || !transformed.diagnostics.is_empty() {
            collector
                .diagnostics
                .push(format!("excluded {id}: replacement is not valid syntax"));
            continue;
        }
        // Different edits can yield the very same file (`!!x` loses either `!`).
        // Keep the first; never drop a mutant of the three older families.
        let first_of_its_kind = distinct.insert(Sha256::digest(code.as_bytes()).to_vec());
        if !first_of_its_kind && !matches!(edit.operator, ARITHMETIC | BOOLEAN | COMPARISON) {
            collector.diagnostics.push(format!(
                "excluded {id}: the edited file is identical to an earlier mutant's"
            ));
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
            map: mapped_code(&file, source, &code, &origins),
            code,
            load_time: edit.load_time,
        });
    }
    Ok(Generated {
        mutants,
        diagnostics: collector.diagnostics,
    })
}

/// The resident module of a file none of whose mutants is selected: the source
/// itself, so the instrumented baseline still validates that the file loads
/// through the transform, as it does for every other `--source` file.
pub fn identity_module(path: &Path, source: &str) -> oximut_core::Mutation {
    let file = path.to_string_lossy().into_owned();
    let origins: Vec<usize> = (0..source.len()).collect();
    oximut_core::Mutation {
        id: "__oximut_resident__".into(),
        map: mapped_code(&file, source, source, &origins),
        file,
        code: source.into(),
    }
}

/// Generate a shared Node-only module containing inline mutation switches.
/// Branches remain in their original lexical context, including await and yield.
/// Files referring to `process` fall back to static mutants because the selector
/// could otherwise resolve to a shadowed binding.
pub fn generate_switched(
    identity: &str,
    path: &Path,
    source: &str,
    families: &Families,
) -> Result<Generated, String> {
    Ok(switch(identity, path, source, families, false)?.0)
}

/// Switched module whose inactive branches also carry coverage probes. One
/// inactive run of this exact code validates switching and observes coverage;
/// mutant runs then execute the validated code, where absent probes are no-ops.
/// Returns the coverage transform: the shared code itself when probes are
/// embedded, the separate probe-only module for static fallbacks, or the reason
/// probes could not be embedded (coverage is then incomplete).
pub fn generate_switched_probed(
    identity: &str,
    path: &Path,
    source: &str,
    families: &Families,
) -> Result<(Generated, Result<oximut_core::Mutation, String>), String> {
    let (generated, switched) = switch(identity, path, source, families, true)?;
    let coverage = match switched {
        Switch::Probed(coverage) => Ok(coverage),
        Switch::Unprobed(reason) => Err(reason),
        Switch::Static => generate_probes(identity, path, source, families),
    };
    Ok((generated, coverage))
}

enum Switch {
    /// Static mutants (or no mutants): each mutant carries its own module.
    Static,
    /// Shared switched module without probes, and why probes are absent.
    Unprobed(String),
    /// Shared switched module with embedded probes, as a coverage transform.
    Probed(oximut_core::Mutation),
}

fn switch(
    identity: &str,
    path: &Path,
    source: &str,
    families: &Families,
    probes: bool,
) -> Result<(Generated, Switch), String> {
    let mut generated = generate(identity, path, source, families)?;
    if generated.mutants.is_empty() {
        return Ok((generated, Switch::Static));
    }
    let allocator = Allocator::default();
    let source_type = SourceType::from_path(path).map_err(|e| e.to_string())?;
    let parsed = Parser::new(&allocator, source, source_type).parse();
    if may_shadow(&parsed.program, "process") {
        generated.diagnostics.push("mutation switching fallback: process identifier or with statement may shadow or modify the Node selector; using fresh static mutants".into());
        return Ok((generated, Switch::Static));
    }
    let global_conflict = if probes {
        probe_conflict(&parsed.program, source)
    } else {
        None
    };
    let mut collector = Collector {
        source,
        families,
        edits: vec![],
        diagnostics: vec![],
        deferred: 0,
        deferred_bodies: Default::default(),
        logical_frames: vec![],
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
                .find(|m| edit.produced(m))
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
        return Ok((generated, Switch::Static));
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
    #[allow(clippy::too_many_arguments)]
    fn render(
        source: &str,
        start: usize,
        end: usize,
        edits: &[(&Edit, &str)],
        skip: &[usize],
        replacement: Option<&Edit>,
        probes: bool,
        output: &mut Output,
    ) {
        let mut cursor = start;
        while cursor < end {
            let next = edits
                .iter()
                .enumerate()
                .filter(|(index, (edit, _))| {
                    !skip.contains(index)
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
            let Some((first, (leader, _))) = next else {
                break;
            };
            let a = leader.expression.start as usize;
            let b = leader.expression.end as usize;
            // Edits sharing one expression (`<` has a boundary and an equality
            // rewrite) are alternatives of it: one switch chain, original last.
            let group: Vec<usize> = edits
                .iter()
                .enumerate()
                .filter(|(index, (edit, _))| {
                    !skip.contains(index)
                        && edit.expression.start == leader.expression.start
                        && edit.expression.end == leader.expression.end
                })
                .map(|(index, _)| index)
                .collect();
            debug_assert!(group.contains(&first));
            let nested: Vec<usize> = skip.iter().chain(&group).copied().collect();
            for &index in &group {
                let (edit, id) = edits[index];
                output.insert(&format!("({ACTIVE_MUTANT} === \"{id}\" ? ("), a);
                render(source, a, b, edits, &nested, Some(edit), probes, output);
                output.insert(") : (", a);
            }
            // The probes precede the original operands, exactly as in
            // `generate_probes`, and only run while every mutant is inactive.
            if probes {
                for &index in &group {
                    let id = edits[index].1;
                    output.insert(&format!("globalThis.__oximut_m3_hit__?.(\"{id}\"), "), a);
                }
                output.insert("(", a);
            }
            render(source, a, b, edits, &nested, None, probes, output);
            if probes {
                output.insert(")", b);
            }
            output.insert(&"))".repeat(group.len()), b);
            cursor = b;
        }
    }
    let rendered = |probes: bool| {
        let mut output = Output {
            code: String::new(),
            origins: vec![],
        };
        render(
            source,
            0,
            source.len(),
            &edits,
            &[],
            None,
            probes,
            &mut output,
        );
        let transformed = Parser::new(&allocator, &output.code, source_type).parse();
        (!transformed.fatal_error && transformed.diagnostics.is_empty()).then_some(output)
    };
    let mut probed = probes && global_conflict.is_none();
    let mut output = if probed { rendered(true) } else { None };
    let mut unprobed = global_conflict.unwrap_or("probes-not-requested");
    if probed && output.is_none() {
        probed = false;
        unprobed = "unsupported-probe-syntax";
    }
    if output.is_none() {
        output = rendered(false);
    }
    let Some(output) = output else {
        generated.diagnostics.push("mutation switching fallback: shared transform is not valid syntax; using fresh static mutants".into());
        return Ok((generated, Switch::Static));
    };
    let file = path.to_string_lossy().into_owned();
    let map = mapped_code(&file, source, &output.code, &output.origins);
    for mutant in &mut generated.mutants {
        mutant.code.clone_from(&output.code);
        mutant.map.clone_from(&map);
    }
    let switched = if probed {
        Switch::Probed(oximut_core::Mutation {
            id: "__oximut_probes__".into(),
            file,
            code: output.code,
            map,
        })
    } else {
        Switch::Unprobed(unprobed.into())
    };
    Ok((generated, switched))
}

/// Why a file cannot carry probes, if it cannot. Probes are free references to
/// `globalThis` calling the reserved `__oximut_m3_hit__` hook, so a file is only
/// refused when such a reference could resolve to something other than the real
/// global or when the file addresses the hook itself. Merely *reading* the
/// global (`typeof globalThis`, `globalThis.Buffer`, `globalThis.x = 1`) is safe:
/// the injected reference resolves exactly as the file's own does.
fn probe_conflict(program: &Program<'_>, source: &str) -> Option<&'static str> {
    if source.contains(PROBE_HOOK) {
        return Some("probe-reserved-name");
    }
    struct Conflict(bool);
    impl<'a> Visit<'a> for Conflict {
        fn visit_binding_identifier(&mut self, it: &BindingIdentifier<'a>) {
            self.0 |= it.name == "globalThis";
        }
        fn visit_with_statement(&mut self, it: &WithStatement<'a>) {
            self.0 = true;
            walk::walk_with_statement(self, it);
        }
        // Writing the binding itself (`globalThis = x`, `globalThis++`,
        // `for (globalThis of xs)`, `({ globalThis } = o)`) could redirect it.
        fn visit_simple_assignment_target(&mut self, it: &SimpleAssignmentTarget<'a>) {
            if let SimpleAssignmentTarget::AssignmentTargetIdentifier(target) = it {
                self.0 |= target.name == "globalThis";
            }
            walk::walk_simple_assignment_target(self, it);
        }
        fn visit_assignment_target_property_identifier(
            &mut self,
            it: &AssignmentTargetPropertyIdentifier<'a>,
        ) {
            self.0 |= it.binding.name == "globalThis";
            walk::walk_assignment_target_property_identifier(self, it);
        }
    }
    let mut found = Conflict(false);
    found.visit_program(program);
    found.0.then_some("probe-globalThis-conflict")
}

const PROBE_HOOK: &str = "__oximut_m3_hit__";

/// The active mutant, as a switch reads it. Reading `process.env` costs about 190 ns
/// (3 ns for a global), and a loop evaluates a switched expression on every
/// iteration, so the switch reads a global that is filled from the environment the
/// first time it is read. Whatever changes the environment of a process whose
/// modules have already run (a worker forked from a precollected template) must
/// set `globalThis.__oximut_active__` too: see `zygote/worker.cjs`.
const ACTIVE_MUTANT: &str =
    "(globalThis.__oximut_active__ ??= process.env.OXIMUT_ACTIVE_MUTANT ?? \"\")";

/// Whether an injected free reference to `name` could resolve to something
/// other than the global: the source names it, or a sloppy-mode `with` scope
/// could supply a same-named property without naming it.
fn may_shadow(program: &Program<'_>, name: &str) -> bool {
    struct Use<'n>(&'n str, bool);
    impl<'a> Visit<'a> for Use<'_> {
        fn visit_identifier_reference(&mut self, it: &IdentifierReference<'a>) {
            self.1 |= it.name == self.0;
        }
        fn visit_binding_identifier(&mut self, it: &BindingIdentifier<'a>) {
            self.1 |= it.name == self.0;
        }
        fn visit_with_statement(&mut self, it: &WithStatement<'a>) {
            self.1 = true;
            walk::walk_with_statement(self, it);
        }
    }
    let mut found = Use(name, false);
    found.visit_program(program);
    found.1
}

/// Probe expression evaluation without changing values, operand order, or the
/// lexical context of await/yield. The collector is installed before imports.
pub fn generate_probes(
    identity: &str,
    path: &Path,
    source: &str,
    families: &Families,
) -> Result<oximut_core::Mutation, String> {
    let generated = generate(identity, path, source, families)?;
    let allocator = Allocator::default();
    let source_type = SourceType::from_path(path).map_err(|e| e.to_string())?;
    let parsed = Parser::new(&allocator, source, source_type).parse();
    if let Some(reason) = probe_conflict(&parsed.program, source) {
        return Err(reason.into());
    }
    let mut collector = Collector {
        source,
        families,
        edits: vec![],
        diagnostics: vec![],
        deferred: 0,
        deferred_bodies: Default::default(),
        logical_frames: vec![],
    };
    collector.visit_program(&parsed.program);
    let mut opens = std::collections::BTreeMap::<usize, Vec<(usize, String)>>::new();
    let mut closes = std::collections::BTreeMap::<usize, usize>::new();
    for mutant in &generated.mutants {
        let edit = collector
            .edits
            .iter()
            .find(|edit| edit.produced(mutant))
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
/// The source with `edit` applied, and the source offset every byte of it came
/// from (the replacement and inserted parentheses map to the token they edit).
fn edited_code(source: &str, edit: &Edit) -> (String, Vec<usize>) {
    let (start, end) = (edit.span.start as usize, edit.span.end as usize);
    let mut parens: Vec<(usize, &str)> = edit
        .wrap
        .iter()
        .flat_map(|span| [(span.start as usize, "("), (span.end as usize, ")")])
        .collect();
    parens.sort_by_key(|(offset, _)| *offset);
    let mut code = String::with_capacity(source.len() + edit.replacement.len() + parens.len());
    let mut origins = Vec::with_capacity(code.capacity());
    let mut cursor = 0;
    let mut push = |text: &str, from: usize, origin: Option<usize>| {
        code.push_str(text);
        match origin {
            Some(origin) => origins.extend(std::iter::repeat_n(origin, text.len())),
            None => origins.extend(from..from + text.len()),
        }
    };
    // Parentheses at or before the token (`(a)&&b` ends one exactly at it) precede
    // the replacement; those at or after its end follow it.
    let (before, after): (Vec<_>, Vec<_>) =
        parens.into_iter().partition(|(offset, _)| *offset <= start);
    for (offset, text) in before {
        push(&source[cursor..offset], cursor, None);
        push(text, offset, Some(offset));
        cursor = offset;
    }
    push(&source[cursor..start], cursor, None);
    push(edit.replacement, start, Some(start));
    cursor = end;
    for (offset, text) in after {
        push(&source[cursor..offset], cursor, None);
        push(text, offset, Some(offset));
        cursor = offset;
    }
    push(&source[cursor..], cursor, None);
    (code, origins)
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

    /// The families that existed before the operator families: the older tests
    /// pin their exact counts and codes, which is also what keeps them honest
    /// about those families staying unchanged.
    fn legacy() -> Families {
        [ARITHMETIC, BOOLEAN, COMPARISON].map(String::from).into()
    }
    fn select(families: &[&str]) -> Families {
        families.iter().map(|family| family.to_string()).collect()
    }
    fn generate(identity: &str, path: &Path, source: &str) -> Result<Generated, String> {
        super::generate(identity, path, source, &legacy())
    }
    fn generate_switched(identity: &str, path: &Path, source: &str) -> Result<Generated, String> {
        super::generate_switched(identity, path, source, &legacy())
    }
    fn generate_switched_probed(
        identity: &str,
        path: &Path,
        source: &str,
    ) -> Result<(Generated, Result<oximut_core::Mutation, String>), String> {
        super::generate_switched_probed(identity, path, source, &legacy())
    }
    fn generate_probes(
        identity: &str,
        path: &Path,
        source: &str,
    ) -> Result<oximut_core::Mutation, String> {
        super::generate_probes(identity, path, source, &legacy())
    }

    fn mutations(source: &str) -> Generated {
        generate("src/a.ts", Path::new("/project/src/a.ts"), source).unwrap()
    }

    /// `load_time` of every mutant, in source order.
    fn load_time(source: &str) -> Vec<bool> {
        mutations(source)
            .mutants
            .iter()
            .map(|mutant| mutant.load_time)
            .collect()
    }

    #[test]
    fn top_level_code_is_load_time() {
        assert_eq!(
            load_time("const a = 1 + 2; if (a > 2) { const b = true; }"),
            [true, true, true]
        );
        assert_eq!(
            load_time("for (let i = 0; i < 3; i++) { 1 * 2; }"),
            [true, true]
        );
    }

    #[test]
    fn function_bodies_run_later() {
        assert_eq!(load_time("function f() { return 1 + 2; }"), [false]);
        assert_eq!(
            load_time("export default function () { return true; }"),
            [false]
        );
        assert_eq!(
            load_time("const f = function () { return 1 + 2; };"),
            [false]
        );
        assert_eq!(load_time("const f = (a) => a + 1;"), [false]);
        assert_eq!(load_time("let f; f = () => 1 * 2;"), [false]);
        assert_eq!(load_time("export default () => 1 - 2;"), [false]);
        // Parentheses and TypeScript wrappers do not change what a value is.
        assert_eq!(
            load_time("const f = ((a: number) => a + 1) as Fn;"),
            [false]
        );
        // Parameter defaults run per call; a load-time default does not.
        assert_eq!(
            load_time("function f(a = 1 + 2) { return a; } const g = (b = 3 * 4) => b;"),
            [false, false]
        );
        // The outer function is deferred, so everything nested in it is too.
        assert_eq!(
            load_time("function f() { const g = () => 1 + 2; return (() => 3 * 4)(); }"),
            [false, false]
        );
    }

    #[test]
    fn immediately_invoked_functions_and_callbacks_are_load_time() {
        assert_eq!(load_time("(function () { return 1 + 2; })();"), [true]);
        assert_eq!(load_time("(() => 1 + 2)();"), [true]);
        assert_eq!(load_time("!function () { return true; }();"), [true]);
        assert_eq!(
            load_time("new (function () { this.x = 1 + 2; })();"),
            [true]
        );
        assert_eq!(load_time("(function (a = 1 + 2) {})();"), [true]);
        // A callback may be invoked synchronously by the call that receives it.
        assert_eq!(load_time("const xs = [1, 2].map((x) => x + 1);"), [true]);
        assert_eq!(
            load_time("register(function () { return 1 * 2; });"),
            [true]
        );
        assert_eq!(
            load_time("const o = new Map([[1, 2]]).forEach(() => true);"),
            [true]
        );
        // Not a recognised position for a deferred function: unknown is load time.
        assert_eq!(load_time("const f = cond ? () => 1 + 2 : null;"), [true]);
        assert_eq!(load_time("const fns = [() => 1 + 2];"), [true]);
        assert_eq!(
            load_time("(function () { return function () { return 1 + 2; }; })();"),
            [true]
        );
        // Inside a deferred function, even an IIFE only runs when called.
        assert_eq!(
            load_time("function f() { return (() => 1 + 2)(); } const x = [1].map((y) => y * 2);"),
            [false, true]
        );
    }

    #[test]
    fn class_members_follow_when_they_run() {
        assert_eq!(
            load_time(
                "class A { m() { return 1 + 2; } get g() { return true; } constructor() { 3 * 4; } }"
            ),
            [false, false, false]
        );
        assert_eq!(
            load_time(
                "class A { static m() { return 1 + 2; } static async n() { return 3 - 4; } }"
            ),
            [false, false]
        );
        // Field initializers, static blocks and computed keys run with the class.
        assert_eq!(
            load_time("class A { x = 1 + 2; static y = true; static { 3 * 4; } ['k' + 'l']() {} }"),
            [true, true, true, true]
        );
        // A function-valued field still runs its body only when called.
        assert_eq!(
            load_time("class A { h = () => 1 + 2; static s = () => 3 * 4; }"),
            [false, false]
        );
        // A class declared inside a function is built when the function runs.
        assert_eq!(
            load_time("function f() { return class { x = 1 + 2; static { 3 * 4; } }; }"),
            [false, false]
        );
        assert_eq!(
            load_time("class A extends mix(1 + 2) { m() { return true; } }"),
            [true, false]
        );
    }

    #[test]
    fn object_members_follow_when_they_run() {
        assert_eq!(
            load_time(
                "const o = { m() { return 1 + 2; }, a: () => 3 * 4, get g() { return true; }, v: 5 - 6, [7 + 8]: 1 };"
            ),
            [false, false, false, true, true]
        );
    }

    #[test]
    fn deserialized_mutants_without_a_classification_are_load_time() {
        let mutant = mutations("function f() { return 1 + 2; }")
            .mutants
            .remove(0);
        assert!(!mutant.load_time);
        let mut json = serde_json::to_value(&mutant).unwrap();
        assert_eq!(json["loadTime"], false);
        json.as_object_mut().unwrap().remove("loadTime");
        let restored: Mutant = serde_json::from_value(json).unwrap();
        assert!(restored.load_time);
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
    fn a_switch_reads_a_global_that_a_forked_worker_can_set() {
        let source = "export const f = (a, b) => a + b;";
        let shared = generate_switched("a.ts", Path::new("/p/a.ts"), source).unwrap();
        let (code, id) = (&shared.mutants[0].code, &shared.mutants[0].id);
        // The global is filled from the environment on the first read...
        let script = format!("{code}\nconsole.log(f(5, 2), globalThis.__oximut_active__);");
        assert_eq!(execute(&script, ""), "7 \n");
        assert_eq!(execute(&script, id), format!("3 {id}\n"));
        // ...and then it, not the environment, decides: a worker forked from a template
        // that already ran the module sets it (zygote/worker.cjs) and the mutant is active.
        let forked = format!(
            "{code}\nconst first = f(5, 2); globalThis.__oximut_active__ = '{id}'; console.log(first, f(5, 2));"
        );
        assert_eq!(execute(&forked, ""), "7 3\n");
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
    fn probed_switching_validates_and_observes_the_code_mutants_execute() {
        let source = r#"
let calls = [];
const value = n => { calls.push(n); return n; };
const object = n => ({ valueOf() { calls.push(`coerce${n}`); return n; } });
const a = (await Promise.resolve(value(8))) + value(3) * value(2);
const b = object(4) + object(2) / object(1);
const c = false && value(99) < value(100);
function* sequence() { return (yield value(9)) - value(2); }
const iterator = sequence();
console.log(JSON.stringify([a, b, c, iterator.next(), iterator.next(7), true, calls]));
"#;
        let path = Path::new("/project/src/a.ts");
        let (shared, coverage) = generate_switched_probed("src/a.ts", path, source).unwrap();
        let coverage = coverage.unwrap();
        let fresh = mutations(source);
        assert_eq!(fresh.mutants.len(), shared.mutants.len());
        // The coverage transform is exactly the shared module mutants execute.
        for mutant in &shared.mutants {
            assert_eq!(mutant.code, coverage.code);
            assert_eq!(mutant.map, coverage.map);
        }
        assert_eq!(coverage.id, "__oximut_probes__");
        assert_eq!(coverage.file, path.to_string_lossy());
        let original = execute(source, "");
        // Inactive: identity, with and without an installed collector.
        assert_eq!(execute(&coverage.code, ""), original);
        let record = |code: &str| {
            execute(
                &format!(
                    "const hits = []; globalThis.__oximut_m3_hit__ = id => {{ hits.push(id); }}; {code}\nprocess.on('exit', () => console.log(JSON.stringify(hits)));"
                ),
                "",
            )
        };
        let probe_only = generate_probes("src/a.ts", path, source).unwrap();
        // Same output and the same ordered hit sequence as probe-only coverage.
        assert_eq!(record(&coverage.code), record(&probe_only.code));
        assert!(record(&coverage.code).starts_with(&original));
        // Active: each mutant behaves like its static module.
        for (static_mutant, switched) in fresh.mutants.iter().zip(&shared.mutants) {
            assert_eq!(static_mutant.id, switched.id);
            assert_eq!(
                execute(&static_mutant.code, ""),
                execute(&switched.code, &switched.id)
            );
            assert!(coverage.code.contains(&format!(
                "globalThis.__oximut_m3_hit__?.(\"{}\")",
                switched.id
            )));
        }
        // Without probes requested, the shared module carries no probes.
        let plain = generate_switched("src/a.ts", path, source).unwrap();
        assert!(!plain.mutants[0].code.contains("__oximut_m3_hit__"));
        assert_eq!(execute(&plain.mutants[0].code, ""), original);
    }

    #[test]
    fn probed_switching_reports_unprobed_and_static_fallbacks() {
        let path = Path::new("/project/src/a.ts");
        // A shadowed globalThis: switching stays shared, probes are refused.
        let source = "function f(globalThis) { return 1; } export const x = 1 + 2;";
        let (generated, coverage) = generate_switched_probed("src/a.ts", path, source).unwrap();
        assert_eq!(coverage.unwrap_err(), "probe-globalThis-conflict");
        assert_eq!(
            generated.mutants[0].code,
            generate_switched("src/a.ts", path, source).unwrap().mutants[0].code
        );
        assert!(!generated.mutants[0].code.contains("__oximut_m3_hit__"));
        // process use: static mutants keep the separate probe-only module.
        let source = "export const x = process.argv.length + 2;";
        let (generated, coverage) = generate_switched_probed("src/a.ts", path, source).unwrap();
        assert!(
            generated
                .diagnostics
                .iter()
                .any(|d| d.contains("switching fallback"))
        );
        assert_eq!(generated.mutants[0].code, source.replace('+', "-"));
        assert_eq!(
            coverage.unwrap().code,
            generate_probes("src/a.ts", path, source).unwrap().code
        );
        // No mutants: the probe-only module is the unchanged source.
        let (generated, coverage) =
            generate_switched_probed("src/a.ts", path, "export const x = 1;").unwrap();
        assert!(generated.mutants.is_empty());
        assert_eq!(coverage.unwrap().code, "export const x = 1;");
    }

    #[test]
    fn with_scopes_fall_back_for_switching_and_probes() {
        // Sloppy scripts only: a with object can supply `process`/`globalThis`.
        let path = Path::new("/project/src/a.js");
        let source = "var scope = {}; with (scope) { var x = 1 + 2; }";
        let (generated, coverage) = generate_switched_probed("src/a.js", path, source).unwrap();
        assert!(
            generated
                .diagnostics
                .iter()
                .any(|d| d.contains("switching fallback") && d.contains("with statement"))
        );
        assert_eq!(generated.mutants[0].code, source.replacen('+', "-", 1));
        assert_eq!(coverage.unwrap_err(), "probe-globalThis-conflict");
        assert_eq!(
            generate_probes("src/a.js", path, source).unwrap_err(),
            "probe-globalThis-conflict"
        );
    }

    #[test]
    fn probes_refuse_only_shadowed_or_rewritten_global_this() {
        for source in [
            "function f(globalThis) { return true; }",
            "var globalThis = {}; export const x = 1 + 2;",
            "import { globalThis } from './g.js'; export const x = 1 + 2;",
            "globalThis = {}; export const x = 1 + 2;",
            "globalThis += 1; export const x = 1 + 2;",
            "globalThis++; export const x = 1 + 2;",
            "for (globalThis of []) {} export const x = 1 + 2;",
            "({ globalThis } = {}); export const x = 1 + 2;",
            "[globalThis] = [{}]; export const x = 1 + 2;",
        ] {
            assert_eq!(
                generate_probes("a.ts", Path::new("a.ts"), source).unwrap_err(),
                "probe-globalThis-conflict",
                "{source}"
            );
        }
        for source in [
            "globalThis.__oximut_m3_hit__ = () => {}; export const x = 1 + 2;",
            "export const x = 1 + 2; delete globalThis['__oximut_m3_hit__'];",
        ] {
            assert_eq!(
                generate_probes("a.ts", Path::new("a.ts"), source).unwrap_err(),
                "probe-reserved-name",
                "{source}"
            );
        }
    }

    #[test]
    fn probes_allow_files_that_read_or_extend_global_this() {
        // Patterns from real libraries (es-toolkit's isBuffer / isPlainObject):
        // reading the global never changes what the injected probe resolves to.
        let path = Path::new("/project/src/a.ts");
        for source in [
            "export function isBuffer(x: unknown) { return typeof globalThis !== 'undefined' && typeof (globalThis as any).Buffer !== 'undefined' && (globalThis as any).Buffer.isBuffer(x) && 1 + 1 === 2; }",
            "globalThis.y = 1; export const x = 1 + 2;",
            "const g = globalThis; export const x = g.y ?? 1 + 2;",
            "export const has = (k) => k in globalThis && 1 + 1 === 2;",
        ] {
            let probe = generate_probes("src/a.ts", path, source).unwrap();
            let (generated, coverage) = generate_switched_probed("src/a.ts", path, source).unwrap();
            let coverage = coverage.unwrap();
            assert!(!generated.mutants.is_empty(), "{source}");
            for mutant in &generated.mutants {
                assert_eq!(probe.code.matches(&mutant.id).count(), 1, "{source}");
                assert_eq!(mutant.code, coverage.code);
            }
        }
        // The probes still observe every mutant of a file that uses globalThis.
        let source = "globalThis.seen = (globalThis.seen ?? 0) + 1; export const x = 1 + 2;";
        let (generated, coverage) = generate_switched_probed("src/a.ts", path, source).unwrap();
        let coverage = coverage.unwrap();
        let record = format!(
            "const hits = []; globalThis.__oximut_m3_hit__ = id => {{ hits.push(id); }}; {}\nprocess.on('exit', () => console.log(JSON.stringify(hits)));",
            coverage.code
        );
        let hits: Vec<String> = serde_json::from_str(execute(&record, "").trim()).unwrap();
        for mutant in &generated.mutants {
            assert!(hits.contains(&mutant.id), "{}", mutant.id);
        }
    }
    // ---- operator families -------------------------------------------------

    const ALL_FAMILIES: [&str; 8] = [
        ARITHMETIC, ASSIGNMENT, BOOLEAN, COMPARISON, EQUALITY, LOGICAL, UNARY, UPDATE,
    ];

    fn only(source: &str, family: &str) -> Generated {
        super::generate(
            "src/a.ts",
            Path::new("/project/src/a.ts"),
            source,
            &select(&[family]),
        )
        .unwrap()
    }

    /// (original, replacement) of every mutant, in source order.
    fn rewrites(source: &str, family: &str) -> Vec<(String, String)> {
        let result = only(source, family);
        assert!(result.mutants.iter().all(|m| m.operator == family));
        result
            .mutants
            .into_iter()
            .map(|m| (m.original, m.replacement))
            .collect()
    }

    fn pairs(list: &[(&str, &str)]) -> Vec<(String, String)> {
        list.iter()
            .map(|(a, b)| (a.to_string(), b.to_string()))
            .collect()
    }

    fn codes(source: &str, family: &str) -> Vec<String> {
        only(source, family)
            .mutants
            .into_iter()
            .map(|m| m.code)
            .collect()
    }

    #[test]
    fn every_family_is_known_to_core_and_generates_something() {
        assert_eq!(
            oximut_core::MUTATOR_FAMILIES.to_vec(),
            ALL_FAMILIES.to_vec()
        );
        let source = "let a = 1; a += 1; a++; const b = -a + 1; const c = true && a === b < a;";
        let all = super::generate("a.ts", Path::new("a.ts"), source, &Families::new()).unwrap();
        for family in ALL_FAMILIES {
            assert!(
                all.mutants.iter().any(|m| m.operator == family),
                "{family} produced nothing"
            );
        }
        assert!(
            all.mutants
                .iter()
                .all(|m| oximut_core::MUTATOR_FAMILIES.contains(&m.operator.as_str()))
        );
    }

    #[test]
    fn selecting_families_generates_exactly_their_mutants() {
        let source = "let a = 1; a += 1; a++; const b = -a; const c = true && !a === b < a;";
        let everything = super::generate(
            "src/a.ts",
            Path::new("/project/src/a.ts"),
            source,
            &Families::new(),
        )
        .unwrap()
        .mutants;
        let mut union = 0;
        for family in ALL_FAMILIES {
            let chosen = only(source, family).mutants;
            let expected: Vec<_> = everything.iter().filter(|m| m.operator == family).collect();
            assert_eq!(
                chosen.iter().map(|m| &m.id).collect::<Vec<_>>(),
                expected.iter().map(|m| &m.id).collect::<Vec<_>>(),
                "{family}"
            );
            union += chosen.len();
        }
        assert_eq!(union, everything.len());
        let two =
            super::generate("a.ts", Path::new("a.ts"), source, &select(&[UPDATE, UNARY])).unwrap();
        assert_eq!(
            two.mutants
                .iter()
                .map(|m| m.operator.as_str())
                .collect::<std::collections::BTreeSet<_>>(),
            [UNARY, UPDATE].into()
        );
    }

    #[test]
    fn equality_operator_flips_equality_and_adds_the_other_half_of_each_comparison() {
        assert_eq!(
            rewrites(
                "a === b; a !== b; a == b; a != b; a < b; a <= b; a > b; a >= b;",
                EQUALITY
            ),
            pairs(&[
                ("===", "!=="),
                ("!==", "==="),
                ("==", "!="),
                ("!=", "=="),
                ("<", ">="),
                ("<=", ">"),
                (">", "<="),
                (">=", "<"),
            ])
        );
        assert_eq!(
            codes("const x = a === b;", EQUALITY),
            ["const x = a !== b;"]
        );
        // Not equality: relational and arithmetic operators outside the table.
        assert!(
            rewrites(
                "a in b; a instanceof B; a % b; a ** b; a | b; a << b;",
                EQUALITY
            )
            .is_empty()
        );
    }

    #[test]
    fn comparisons_split_between_boundary_and_equality_without_overlap() {
        let source = "a < b; a <= b; a > b; a >= b;";
        let both = super::generate(
            "a.ts",
            Path::new("a.ts"),
            source,
            &select(&[COMPARISON, EQUALITY]),
        )
        .unwrap();
        let mut seen = std::collections::BTreeSet::new();
        for m in &both.mutants {
            assert!(
                seen.insert((m.location.start.offset, m.replacement.clone())),
                "duplicate edit {} -> {}",
                m.original,
                m.replacement
            );
            assert_ne!(m.code, source);
        }
        assert_eq!(both.mutants.len(), 8);
        let ids: std::collections::BTreeSet<_> = both.mutants.iter().map(|m| &m.id).collect();
        assert_eq!(ids.len(), 8);
        let codes: std::collections::BTreeSet<_> = both.mutants.iter().map(|m| &m.code).collect();
        assert_eq!(codes.len(), 8);
    }

    #[test]
    fn logical_operator_swaps_and_or_and_turns_coalescing_into_and() {
        assert_eq!(
            rewrites("a && b; a || b; a ?? b;", LOGICAL),
            pairs(&[("&&", "||"), ("||", "&&"), ("??", "&&")])
        );
        assert_eq!(
            codes("x = (a ?? b) || c && d;", LOGICAL),
            [
                "x = (a && b) || c && d;",
                "x = (a ?? b) && c && d;",
                "x = (a ?? b) || c || d;"
            ]
        );
    }

    #[test]
    fn a_swapped_logical_operator_regroups_nothing_around_it() {
        // Swapping one `&&` of a chain must not turn `a && b && c` into the
        // differently grouped `a || b && c`: that is another program.
        assert_eq!(
            codes("x = a && b && c;", LOGICAL),
            ["x = (a || b) && c;", "x = a && b || c;"]
        );
        assert_eq!(
            codes("x = a || b || c;", LOGICAL),
            ["x = a && b || c;", "x = (a || b) && c;"]
        );
        assert_eq!(
            codes("x = a ?? b ?? c;", LOGICAL),
            ["x = (a && b) ?? c;", "x = (a ?? b) && c;"]
        );
        // The deepest operand of a longer chain, without spaces to hide behind.
        assert_eq!(
            codes("x = a&&b&&c&&d;", LOGICAL),
            ["x = (a||b)&&c&&d;", "x = (a&&b||c)&&d;", "x = a&&b&&c||d;"]
        );
        // Mixed levels: only the operand the new operator out-binds is wrapped.
        assert_eq!(
            codes("x = a || b && c;", LOGICAL),
            ["x = a && b && c;", "x = a || b || c;"]
        );
    }

    #[test]
    fn a_wrapped_logical_mutant_keeps_a_source_map_for_every_byte() {
        let source = "x = a && b && c;";
        let mutant = &only(source, LOGICAL).mutants[0];
        assert_eq!(mutant.code, "x = (a || b) && c;");
        let mappings = mutant.map["mappings"].as_str().unwrap();
        assert!(!mappings.is_empty() && !mappings.contains(';'));
    }

    #[test]
    fn unary_operator_flips_signs_and_removes_bitwise_and_logical_not() {
        assert_eq!(
            rewrites("-a; +a; ~a; !a; typeof a; void a; delete o.a;", UNARY),
            pairs(&[("-", "+"), ("+", "-"), ("~", ""), ("!", "")])
        );
        assert_eq!(
            codes("if (!ready && -x < 0) {}", UNARY),
            ["if (ready && -x < 0) {}", "if (!ready && +x < 0) {}"]
        );
        // Negative literals are unary expressions too.
        assert_eq!(codes("const n = -1;", UNARY), ["const n = +1;"]);
        // Negation removal belongs to unary-operator, not boolean-literal.
        assert!(rewrites("const c = !a;", BOOLEAN).is_empty());
    }

    #[test]
    fn update_operator_swaps_increment_and_decrement_in_both_positions() {
        assert_eq!(
            rewrites("a++; a--; ++a; --a; o.n++; arr[i]--;", UPDATE),
            pairs(&[
                ("++", "--"),
                ("--", "++"),
                ("++", "--"),
                ("--", "++"),
                ("++", "--"),
                ("--", "++"),
            ])
        );
        assert_eq!(
            codes("for (let i = 0; i < n; i++) {}", UPDATE),
            ["for (let i = 0; i < n; i--) {}"]
        );
        assert_eq!(codes("++count;", UPDATE), ["--count;"]);
    }

    #[test]
    fn assignment_operator_follows_the_strykerjs_table() {
        let table = [
            ("+=", "-="),
            ("-=", "+="),
            ("*=", "/="),
            ("/=", "*="),
            ("%=", "*="),
            ("<<=", ">>="),
            (">>=", "<<="),
            ("&=", "|="),
            ("|=", "&="),
            ("&&=", "||="),
            ("||=", "&&="),
            ("??=", "&&="),
        ];
        let source: String = table.iter().map(|(op, _)| format!("a {op} b; ")).collect();
        assert_eq!(rewrites(&source, ASSIGNMENT), pairs(&table));
        // Plain assignment and the operators StrykerJS leaves alone.
        assert!(rewrites("a = b; a **= b; a >>>= b; a ^= b; let c = 1;", ASSIGNMENT).is_empty());
        assert_eq!(
            codes("total += item.price;", ASSIGNMENT),
            ["total -= item.price;"]
        );
    }

    #[test]
    fn operator_tokens_are_located_between_operands_not_in_comments() {
        let cases = [
            (
                EQUALITY,
                "a /* === */ === /* !== */ b; // ===",
                "a /* === */ !== /* !== */ b; // ===",
            ),
            (
                LOGICAL,
                "a /* || */ && /* ?? */ b;",
                "a /* || */ || /* ?? */ b;",
            ),
            (UNARY, "- /* + */ a;", "+ /* + */ a;"),
            (UNARY, "! /* ! */ a;", " /* ! */ a;"),
            (UNARY, "~/* ~ */a;", "/* ~ */a;"),
            (UPDATE, "a /* -- */ ++;", "a /* -- */ --;"),
            (UPDATE, "++ /* ++ */ a;", "-- /* ++ */ a;"),
            (
                ASSIGNMENT,
                "a /* -= */ += /* += */ b;",
                "a /* -= */ -= /* += */ b;",
            ),
            (ASSIGNMENT, "a // +=\n += b;", "a // +=\n -= b;"),
        ];
        for (family, source, expected) in cases {
            assert_eq!(codes(source, family), [expected], "{source}");
        }
        // Operator-looking text only in comments: nothing to find, nothing to mutate.
        assert!(only("a /* && */ ;", LOGICAL).mutants.is_empty());
    }

    #[test]
    fn parenthesized_operands_and_typescript_wrappers_are_handled() {
        assert_eq!(codes("(a)++;", UPDATE), ["(a)--;"]);
        assert_eq!(codes("(a as number)++;", UPDATE), ["(a as number)--;"]);
        assert_eq!(codes("a!++;", UPDATE), ["a!--;"]);
        assert_eq!(
            codes("(a as T) === (b satisfies U);", EQUALITY),
            ["(a as T) !== (b satisfies U);"]
        );
        assert_eq!(codes("o!.n ??= 1;", ASSIGNMENT), ["o!.n &&= 1;"]);
        assert_eq!(codes("(a) += (1);", ASSIGNMENT), ["(a) -= (1);"]);
        assert_eq!(codes("++(a);", UPDATE), ["--(a);"]);
        assert_eq!(codes("((a))++;", UPDATE), ["((a))--;"]);
    }

    #[test]
    fn edits_that_yield_the_same_file_produce_one_mutant() {
        // Removing either `!` of `!!x` (or `~` of `~~x`) leaves the same program.
        let result = only("if (!!ready) {} const m = ~~n;", UNARY);
        assert_eq!(
            result
                .mutants
                .iter()
                .map(|m| m.code.as_str())
                .collect::<Vec<_>>(),
            [
                "if (!ready) {} const m = ~~n;",
                "if (!!ready) {} const m = ~n;"
            ]
        );
        assert!(result.diagnostics.iter().any(|d| d.contains("identical")));
        // Distinct edits of nested signs stay distinct.
        assert_eq!(codes("x = - -y;", UNARY), ["x = + -y;", "x = - +y;"]);
        // Mutants of the older families are never merged.
        let both = super::generate("a.ts", Path::new("a.ts"), "a + b; a + b;", &legacy()).unwrap();
        assert_eq!(both.mutants.len(), 2);
    }

    #[test]
    fn prefix_replacements_never_fuse_with_the_preceding_token() {
        for (family, source) in [
            (UNARY, "x = a-+b;"),
            (UNARY, "x = a+-b;"),
            (UNARY, "x = typeof!a;"),
            (UNARY, "x = typeof~a;"),
            (UPDATE, "x = a-++b;"),
            (UPDATE, "x = a+--b;"),
        ] {
            let result = only(source, family);
            assert!(result.mutants.is_empty(), "{source}");
            assert!(
                result.diagnostics.iter().any(|d| d.contains("fuse")),
                "{source}: {:?}",
                result.diagnostics
            );
        }
        assert_eq!(codes("x = a - +b;", UNARY), ["x = a - -b;"]);
        assert_eq!(codes("x = typeof !a;", UNARY), ["x = typeof a;"]);
        assert_eq!(codes("x = a - ++b;", UPDATE), ["x = a - --b;"]);
        // A replacement that is not a valid program never reaches a test run.
        let result = only("!function () { return 1; }();", UNARY);
        assert!(result.mutants.is_empty());
        assert!(
            result
                .diagnostics
                .iter()
                .any(|d| d.contains("not valid syntax"))
        );
    }

    #[test]
    fn operator_families_skip_types_ambient_code_enums_and_decorators() {
        let source = "type A = -1 | typeof x; interface I { a: -1; [k: string]: 1 }; \
            declare const d: -1; declare let u: number; declare function f(a: -1): void; \
            enum E { X = -1, Y = !1 ? 1 : 2, Z = 1 === 1 ? 1 : 2 }; \
            namespace N { export const n = -1; export let m = 0; m++; } \
            abstract class B { abstract [x === y](): void; abstract p: number; declare q: number; } \
            @dec(a && b) class C { @dec(-a) m() {} } \
            const real: -1 = -1;";
        for family in [UNARY, UPDATE, EQUALITY, LOGICAL, ASSIGNMENT] {
            let result = only(source, family);
            let expected = usize::from(family == UNARY);
            assert_eq!(
                result.mutants.len(),
                expected,
                "{family}: {:?}",
                result.mutants.iter().map(|m| &m.code).collect::<Vec<_>>()
            );
            if family == UNARY {
                assert!(result.mutants[0].code.ends_with("const real: -1 = +1;"));
            }
        }
        // Declaration files hold only types.
        assert!(
            super::generate(
                "a.d.ts",
                Path::new("a.d.ts"),
                "declare const x: -1;",
                &Families::new()
            )
            .unwrap()
            .mutants
            .is_empty()
        );
    }

    #[test]
    fn operator_families_classify_load_time_like_the_older_ones() {
        let load = |source: &str, family: &str| -> Vec<bool> {
            only(source, family)
                .mutants
                .iter()
                .map(|m| m.load_time)
                .collect()
        };
        assert_eq!(
            load(
                "let n = 0; n++; function f() { n++; } const g = () => n--;",
                UPDATE
            ),
            [true, false, false]
        );
        assert_eq!(
            load(
                "let n = 0; n += 1; const f = function () { n *= 2; }; class K { m() { n -= 1; } x = n ||= 1; }",
                ASSIGNMENT
            ),
            [true, false, false, true]
        );
        assert_eq!(
            load(
                "const a = x && y; function f() { return x ?? y; } [1].map(() => x || y);",
                LOGICAL
            ),
            [true, false, true]
        );
        assert_eq!(
            load(
                "const a = -x; const f = () => !x; (function () { return +x; })();",
                UNARY
            ),
            [true, false, true]
        );
        assert_eq!(
            load(
                "if (a === b) {} function f(p = a != b) { return a < b; }",
                EQUALITY
            ),
            [true, false, false]
        );
    }

    #[test]
    fn identities_of_the_first_three_families_do_not_depend_on_the_newer_ones() {
        let source = "export function f(a: number, b: number) {\n  let n = 0;\n  n += a;\n  n++;\n  if (!(a < b) && a === b || a >= b) { return -a + b * 2; }\n  return true;\n}\n";
        let path = Path::new("/project/src/a.ts");
        let legacy_only = super::generate("src/a.ts", path, source, &legacy()).unwrap();
        let everything = super::generate("src/a.ts", path, source, &Families::new()).unwrap();
        let in_all: Vec<_> = everything
            .mutants
            .iter()
            .filter(|m| legacy().contains(&m.operator))
            .collect();
        assert_eq!(legacy_only.mutants.len(), in_all.len());
        assert!(legacy_only.mutants.len() >= 5);
        for (alone, within) in legacy_only.mutants.iter().zip(in_all) {
            assert_eq!(alone.id, within.id);
            assert_eq!(alone.code, within.code);
            assert_eq!(alone.location.start.offset, within.location.start.offset);
        }
        assert!(everything.mutants.len() > legacy_only.mutants.len());
        // Identities recorded from the generator before the operator families
        // existed (`oximut --list` of 1.0.0-rc.3): reports, caches and the parity
        // corpus key on them, so they must never move.
        let recorded = [
            (
                "a06a34022ad0b5792692dbfd55308a04dad43b259550df17f8bf1f59efa1e2f5",
                COMPARISON,
                "<",
                "<=",
            ),
            (
                "e992f85d3dda554d624479074b08dbc7ac7cdc1556f963de06f49b534ce39212",
                COMPARISON,
                ">=",
                ">",
            ),
            (
                "b5bf3893b55f74fa705ac0c877f4b6769d5cb48d8e14faf0d71540a5d8d33d03",
                ARITHMETIC,
                "+",
                "-",
            ),
            (
                "7c0af8a5b501731a1f46e6a0e191789f5b7a558e00ba59e4a4452d6ecd67b47d",
                ARITHMETIC,
                "*",
                "/",
            ),
            (
                "7a009c772371bf0c78325715054d30aa0fe0556863b70cf23685abc56557384b",
                BOOLEAN,
                "true",
                "false",
            ),
        ];
        for selection in [legacy(), Families::new()] {
            let generated = super::generate("src/a.ts", path, source, &selection).unwrap();
            for (id, family, original, replacement) in recorded {
                let found = generated.mutants.iter().find(|m| m.id == id);
                let found = found.unwrap_or_else(|| panic!("{family} {original} lost its id"));
                assert_eq!(
                    (
                        found.operator.as_str(),
                        found.original.as_str(),
                        found.replacement.as_str()
                    ),
                    (family, original, replacement)
                );
            }
        }
    }

    #[test]
    fn same_span_alternatives_switch_and_probe_like_their_static_mutants() {
        // `<` has a boundary and an equality rewrite on one token; `a < b ? x : y`
        // nests more of them, `n++`/`!n` sit inside other expressions.
        let source = r#"
let calls = [];
const value = n => { calls.push(n); return n; };
let n = value(1);
const a = value(2) < value(3) ? value(4) >= value(4) : value(5) !== value(6);
let m = 5; m += value(2); m++; --m;
const b = (!n && value(7)) || (value(0) ?? value(8));
const c = -value(9) + +value(3), t = true;
console.log(JSON.stringify([a, b, c, t, m, n, calls]));
"#;
        let path = Path::new("/project/src/a.ts");
        let all = Families::new();
        let fresh = super::generate("src/a.ts", path, source, &all).unwrap();
        let shared = super::generate_switched("src/a.ts", path, source, &all).unwrap();
        assert_eq!(fresh.mutants.len(), shared.mutants.len());
        assert!(fresh.mutants.len() >= 15, "{}", fresh.mutants.len());
        for family in ALL_FAMILIES {
            assert!(
                fresh.mutants.iter().any(|m| m.operator == family),
                "{family}"
            );
        }
        assert_eq!(execute(source, ""), execute(&shared.mutants[0].code, ""));
        for (static_mutant, switched) in fresh.mutants.iter().zip(&shared.mutants) {
            assert_eq!(static_mutant.id, switched.id);
            assert_eq!(
                execute(&static_mutant.code, ""),
                execute(&switched.code, &switched.id),
                "{} {} -> {}",
                static_mutant.operator,
                static_mutant.original,
                static_mutant.replacement
            );
        }
        // Probes observe every mutant exactly once per module, in the same order
        // whether embedded in the switched module or generated on their own.
        let (generated, coverage) =
            super::generate_switched_probed("src/a.ts", path, source, &all).unwrap();
        let coverage = coverage.unwrap();
        let probe_only = super::generate_probes("src/a.ts", path, source, &all).unwrap();
        for mutant in &generated.mutants {
            assert_eq!(probe_only.code.matches(&mutant.id).count(), 1);
            assert!(coverage.code.contains(&format!(
                "globalThis.__oximut_m3_hit__?.(\"{}\")",
                mutant.id
            )));
        }
        assert_eq!(execute(&coverage.code, ""), execute(source, ""));
        let record = |code: &str| {
            execute(
                &format!(
                    "const hits = []; globalThis.__oximut_m3_hit__ = id => {{ hits.push(id); }}; {code}\nprocess.on('exit', () => console.log(JSON.stringify(hits)));"
                ),
                "",
            )
        };
        assert_eq!(record(&coverage.code), record(&probe_only.code));
        for (static_mutant, switched) in fresh.mutants.iter().zip(&generated.mutants) {
            assert_eq!(
                execute(&static_mutant.code, ""),
                execute(&switched.code, &switched.id)
            );
        }
    }

    #[test]
    fn a_file_without_selected_mutants_has_an_identity_resident_module() {
        let source = "export const f = (a: number) => (a < 1 ? 1 : 2);\n// é\n";
        let path = Path::new("/project/src/a.ts");
        let module = identity_module(path, source);
        assert_eq!(module.id, "__oximut_resident__");
        assert_eq!(module.code, source);
        assert_eq!(module.file, path.to_string_lossy());
        assert_eq!(module.map["sourcesContent"][0], source);
        assert_eq!(module.map["version"], 3);
        assert!(
            super::generate("a.ts", path, source, &select(&[ARITHMETIC]))
                .unwrap()
                .mutants
                .is_empty()
        );
    }

    #[test]
    fn a_selection_shapes_the_shared_module() {
        let source = "export const f = (a: number, b: number) => (a < b ? -a : !b);";
        let path = Path::new("/project/src/a.ts");
        let all = super::generate_switched("a.ts", path, source, &Families::new()).unwrap();
        let comparison =
            super::generate_switched("a.ts", path, source, &select(&[COMPARISON])).unwrap();
        assert_eq!(comparison.mutants.len(), 1);
        assert!(all.mutants.len() > 1);
        // The unselected families leave no switch behind.
        assert_eq!(
            comparison.mutants[0]
                .code
                .matches("OXIMUT_ACTIVE_MUTANT")
                .count(),
            1
        );
        assert_eq!(
            all.mutants[0].code.matches("OXIMUT_ACTIVE_MUTANT").count(),
            all.mutants.len()
        );
    }
}
