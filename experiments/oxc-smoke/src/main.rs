//! M0 dependency/grammar smoke check. This does not generate mutations.
use std::{env, fs, path::Path, process::ExitCode};

use oxc_allocator::Allocator;
use oxc_parser::Parser;
use oxc_span::SourceType;

fn parse(path: &Path, source: &str) -> Result<usize, String> {
    let source_type = SourceType::from_path(path).map_err(|error| error.to_string())?;
    let allocator = Allocator::default();
    let result = Parser::new(&allocator, source, source_type).parse();
    if !result.diagnostics.is_empty() || result.fatal_error {
        return Err(format!("parse failed: {:?}", result.diagnostics));
    }
    Ok(result.program.body.len())
}

fn main() -> ExitCode {
    let files: Vec<_> = env::args_os().skip(1).collect();
    if files.is_empty() {
        eprintln!("usage: oximut-oxc-smoke <source.js|source.ts|source.tsx> [...]");
        return ExitCode::from(2);
    }
    for file in files {
        let path = Path::new(&file);
        let result = fs::read_to_string(path)
            .map_err(|error| error.to_string())
            .and_then(|source| parse(path, &source));
        match result {
            Ok(statements) => println!("{}: {statements} top-level statements", path.display()),
            Err(error) => {
                eprintln!("{}: {error}", path.display());
                return ExitCode::FAILURE;
            }
        }
    }
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_typescript_and_tsx_using_the_path() {
        assert_eq!(
            parse(Path::new("input.ts"), "export const answer: number = 42;"),
            Ok(1)
        );
        assert_eq!(
            parse(Path::new("input.tsx"), "export const view = <div />;"),
            Ok(1)
        );
    }

    #[test]
    fn rejects_invalid_source_and_unsupported_extensions() {
        assert!(parse(Path::new("input.ts"), "export const = ;").is_err());
        assert!(parse(Path::new("input.txt"), "42").is_err());
    }
}
