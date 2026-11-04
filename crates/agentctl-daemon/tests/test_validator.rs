use agentctl_daemon::core::schemas::{PermissionToken, ProposedAction};
use agentctl_daemon::core::validator::validate;
use chrono::{Duration, Utc};
use proptest::prelude::*;
use std::fs::{self, File};
use std::os::unix::fs::symlink;
use std::path::PathBuf;
use tempfile::tempdir;
use url::Url;

fn make_valid_token(allowed_paths: Vec<PathBuf>, allowed_domains: Vec<String>) -> PermissionToken {
    PermissionToken {
        id: "tok-valid-1".to_string(),
        allowed_actions: vec![
            "file_read".to_string(),
            "file_write".to_string(),
            "file_move".to_string(),
            "file_delete".to_string(),
            "browser_nav".to_string(),
            "shell_exec".to_string(),
        ],
        allowed_paths,
        allowed_domains,
        expires_at: Utc::now() + Duration::hours(1),
        revoked: false,
    }
}

#[test]
fn test_symlink_escape_denied() {
    let safe_dir = tempdir().unwrap();
    let outside_dir = tempdir().unwrap();

    let outside_file = outside_dir.path().join("secret.txt");
    File::create(&outside_file).unwrap();

    // Create a symlink inside safe_dir pointing to outside_file
    let symlink_path = safe_dir.path().join("link_to_outside.txt");
    symlink(&outside_file, &symlink_path).unwrap();

    let token = make_valid_token(vec![safe_dir.path().to_path_buf()], vec![]);
    let now = Utc::now();

    let action = ProposedAction::FileRead {
        path: symlink_path,
    };

    let result = validate(&action, &token, now);
    assert!(
        !result.allowed,
        "Reading through symlink escaping allowed root must be DENIED"
    );
    assert!(result.reason.contains("outside allowed directories"));
}

#[test]
fn test_path_traversal_dot_dot_denied() {
    let safe_dir = tempdir().unwrap();
    let token = make_valid_token(vec![safe_dir.path().to_path_buf()], vec![]);
    let now = Utc::now();

    let attack_path = safe_dir
        .path()
        .join("../../../../../etc/passwd");

    let action = ProposedAction::FileRead {
        path: attack_path,
    };

    let result = validate(&action, &token, now);
    assert!(
        !result.allowed,
        "Path traversal with '..' must be DENIED"
    );
}

#[test]
fn test_null_byte_denied() {
    let safe_dir = tempdir().unwrap();
    let token = make_valid_token(vec![safe_dir.path().to_path_buf()], vec![]);
    let now = Utc::now();

    let action = ProposedAction::FileWrite {
        path: PathBuf::from(format!("{}/safe.txt\0attack", safe_dir.path().display())),
        content: "malicious".to_string(),
    };

    let result = validate(&action, &token, now);
    assert!(!result.allowed, "Null byte in path must be DENIED");
}

#[test]
fn test_expired_and_revoked_tokens() {
    let safe_dir = tempdir().unwrap();
    let mut token = make_valid_token(vec![safe_dir.path().to_path_buf()], vec![]);
    let now = Utc::now();

    // Expired
    token.expires_at = now - Duration::seconds(10);
    let action = ProposedAction::FileRead {
        path: safe_dir.path().join("any.txt"),
    };
    let res_expired = validate(&action, &token, now);
    assert!(!res_expired.allowed);
    assert!(res_expired.reason.contains("expired"));

    // Revoked
    token.expires_at = now + Duration::hours(1);
    token.revoked = true;
    let res_revoked = validate(&action, &token, now);
    assert!(!res_revoked.allowed);
    assert!(res_revoked.reason.contains("revoked"));
}

#[test]
fn test_browser_nav_domain_whitelist() {
    let token = make_valid_token(vec![], vec!["api.github.com".to_string()]);
    let now = Utc::now();

    let action_good = ProposedAction::BrowserNav {
        url: Url::parse("https://api.github.com/repos").unwrap(),
    };
    let res_good = validate(&action_good, &token, now);
    assert!(res_good.allowed);

    let action_bad = ProposedAction::BrowserNav {
        url: Url::parse("https://evil-phishing.com/login").unwrap(),
    };
    let res_bad = validate(&action_bad, &token, now);
    assert!(!res_bad.allowed);
    assert!(res_bad.reason.contains("not in allowed_domains"));
}

#[test]
fn test_valid_file_write_in_allowed_root_allowed() {
    let safe_dir = tempdir().unwrap();
    let token = make_valid_token(vec![safe_dir.path().to_path_buf()], vec![]);
    let now = Utc::now();

    let target_file = safe_dir.path().join("subdir").join("valid.txt");
    fs::create_dir_all(safe_dir.path().join("subdir")).unwrap();

    let action = ProposedAction::FileWrite {
        path: target_file,
        content: "safe content".to_string(),
    };

    let result = validate(&action, &token, now);
    assert!(result.allowed, "Valid file in safe dir must be allowed");
}

// Proptest fuzzing for paths
proptest! {
    #[test]
    fn proptest_random_paths_never_panic(s in "\\PC*") {
        let safe_dir = tempdir().unwrap();
        let token = make_valid_token(vec![safe_dir.path().to_path_buf()], vec![]);
        let now = Utc::now();

        let action = ProposedAction::FileRead {
            path: PathBuf::from(s),
        };

        // Must never panic, must return a boolean allowed
        let _ = validate(&action, &token, now);
    }
}

