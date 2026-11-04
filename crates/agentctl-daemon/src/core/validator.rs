use crate::core::schemas::{PermissionToken, ProposedAction, ValidatorResult};
use chrono::{DateTime, Utc};
use std::path::{Component, Path, PathBuf};
use unicode_normalization::UnicodeNormalization;

/// Safely checks and normalizes a filesystem path, ensuring it strictly resides
/// within one of the allowed directory roots.
///
/// Handles:
/// - Null byte injection
/// - Unicode normalization (NFC)
/// - Path traversal (`..`)
/// - Symlink escape resolution
/// - Non-existent file creation inside allowed parents
pub fn validate_path_containment(path: &Path, allowed_roots: &[PathBuf]) -> Result<PathBuf, String> {
    let path_str = path.to_str().ok_or_else(|| "Path is not valid UTF-8".to_string())?;

    // Rule 4: Deny null bytes
    if path_str.contains('\0') {
        return Err("Path contains forbidden null byte".to_string());
    }

    // Rule 3: Unicode NFC normalization
    let normalized_str: String = path_str.nfc().collect();
    let normalized_path = PathBuf::from(normalized_str);

    if allowed_roots.is_empty() {
        return Err("No allowed paths configured in token".to_string());
    }

    // Canonicalize all allowed roots
    let canonical_roots: Vec<PathBuf> = allowed_roots
        .iter()
        .filter_map(|r| r.canonicalize().ok())
        .collect();

    if canonical_roots.is_empty() {
        return Err("Allowed directory roots could not be resolved".to_string());
    }

    // Check for explicit '..' components that might attempt to walk up before canonicalization
    let mut depth: isize = 0;
    for comp in normalized_path.components() {
        match comp {
            Component::ParentDir => depth -= 1,
            Component::Normal(_) => depth += 1,
            _ => {}
        }
        if depth < 0 {
            return Err("Path traversal attempt with '..' escaping root".to_string());
        }
    }

    // Resolve path: if file exists, canonicalize directly.
    // If not, canonicalize parent and append file name.
    let resolved_path = match normalized_path.canonicalize() {
        Ok(canon) => canon,
        Err(_) => {
            // Find lowest existing ancestor
            let mut curr = normalized_path.clone();
            let mut tail = PathBuf::new();
            while !curr.exists() {
                if let Some(file_name) = curr.file_name() {
                    tail = Path::new(file_name).join(tail);
                }
                if !curr.pop() {
                    break;
                }
            }

            if !curr.exists() {
                return Err("Path base does not exist and cannot be verified".to_string());
            }

            let canon_base = curr
                .canonicalize()
                .map_err(|e| format!("Failed to resolve ancestor path: {}", e))?;
            canon_base.join(tail)
        }
    };

    // Check containment in at least one allowed root
    let is_contained = canonical_roots
        .iter()
        .any(|root| resolved_path.starts_with(root));

    if !is_contained {
        return Err(format!(
            "Path '{}' (resolved as '{}') is outside allowed directories",
            normalized_path.display(),
            resolved_path.display()
        ));
    }

    Ok(resolved_path)
}

/// Validates whether a proposed action is allowed under the given permission token and time.
pub fn validate(action: &ProposedAction, token: &PermissionToken, now: DateTime<Utc>) -> ValidatorResult {
    // 1. Check token revocation & expiration
    if token.revoked {
        return ValidatorResult::deny("Permission token has been revoked");
    }

    if now > token.expires_at {
        return ValidatorResult::deny("Permission token has expired");
    }

    // 2. Check if action type is allowed
    let action_name = action.action_type_name();
    if !token.allowed_actions.iter().any(|a| a == action_name) {
        return ValidatorResult::deny(format!(
            "Action '{}' is not in allowed_actions list",
            action_name
        ));
    }

    // 3. Action-specific constraints
    match action {
        ProposedAction::FileRead { path }
        | ProposedAction::FileWrite { path, .. }
        | ProposedAction::FileDelete { path } => {
            if let Err(e) = validate_path_containment(path, &token.allowed_paths) {
                return ValidatorResult::deny(e);
            }
        }
        ProposedAction::FileMove { src, dst } => {
            if let Err(e) = validate_path_containment(src, &token.allowed_paths) {
                return ValidatorResult::deny(format!("Move source invalid: {}", e));
            }
            if let Err(e) = validate_path_containment(dst, &token.allowed_paths) {
                return ValidatorResult::deny(format!("Move destination invalid: {}", e));
            }
        }
        ProposedAction::BrowserNav { url } => {
            let host = match url.host_str() {
                Some(h) => h,
                None => return ValidatorResult::deny("Browser URL has no valid host"),
            };

            let is_allowed = token
                .allowed_domains
                .iter()
                .any(|d| host == d || host.ends_with(&format!(".{}", d)));

            if !is_allowed {
                return ValidatorResult::deny(format!(
                    "Domain '{}' is not in allowed_domains list",
                    host
                ));
            }
        }
        ProposedAction::ShellExec { .. } | ProposedAction::LogMessage { .. } => {
            // Checked via token.allowed_actions above
        }
    }

    ValidatorResult::allow()
}
