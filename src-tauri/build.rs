fn main() {
    println!("cargo:rustc-link-arg=-Wl,-rpath,$ORIGIN/../lib/Handy");
    stage_transcribe_runtime_libs();

    tauri_build::build()
}

/// Stage the Linux transcribe-cpp shared runtime and loadable backend modules
/// for the deb package's app-private `/usr/lib/Handy` directory.
fn stage_transcribe_runtime_libs() {
    use std::collections::{BTreeMap, BTreeSet};
    use std::path::PathBuf;

    println!("cargo:rerun-if-env-changed=DEP_TRANSCRIBE_CPP_RUNTIME_DIR");
    println!("cargo:rerun-if-env-changed=DEP_TRANSCRIBE_CPP_MODULE_DIR");

    let Some(runtime_dir) = std::env::var_os("DEP_TRANSCRIBE_CPP_RUNTIME_DIR") else {
        return;
    };

    let mut dirs = BTreeSet::new();
    dirs.insert(PathBuf::from(runtime_dir));
    if let Some(module_dir) = std::env::var_os("DEP_TRANSCRIBE_CPP_MODULE_DIR") {
        dirs.insert(PathBuf::from(module_dir));
    }

    let destination =
        PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap()).join("transcribe-libs");
    let _ = std::fs::remove_dir_all(&destination);
    std::fs::create_dir_all(&destination).expect("create transcribe-libs staging dir");

    let mut libraries: BTreeMap<String, PathBuf> = BTreeMap::new();
    for dir in &dirs {
        println!("cargo:rerun-if-changed={}", dir.display());
        for entry in std::fs::read_dir(dir)
            .unwrap_or_else(|error| panic!("read {}: {error}", dir.display()))
            .flatten()
        {
            let source = entry.path();
            let name = source
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or("");
            if name.ends_with(".so") || name.contains(".so.") {
                libraries.insert(name.to_string(), source);
            }
        }
    }

    // Keep one dereferenced file per library: the SONAME for linked libraries
    // and the bare `.so` name for loadable backend modules.
    let mut best: BTreeMap<&str, (&str, &PathBuf, usize)> = BTreeMap::new();
    for (name, source) in &libraries {
        let Some((stem, depth)) = split_versioned_so(name) else {
            continue;
        };
        let rank = if depth == 1 { 0 } else { depth + 1 };
        match best.get(stem) {
            Some(&(_, _, existing_rank)) if existing_rank <= rank => {}
            _ => {
                best.insert(stem, (name, source, rank));
            }
        }
    }

    let mut copied = 0usize;
    for &(name, source, _) in best.values() {
        std::fs::copy(source, destination.join(name))
            .unwrap_or_else(|error| panic!("copy {}: {error}", source.display()));
        copied += 1;
    }
    if copied == 0 {
        panic!(
            "no transcribe-cpp runtime libraries found under {dirs:?}; the Linux dynamic-backends build cannot run"
        );
    }
    println!("cargo:warning=Staged {copied} transcribe-cpp runtime library file(s)");
}

/// Split `libfoo.so`, `libfoo.so.0`, or `libfoo.so.0.1.3` into its stem and
/// numeric version depth.
fn split_versioned_so(name: &str) -> Option<(&str, usize)> {
    let index = name.find(".so")?;
    let (stem, rest) = (&name[..index], &name[index + 3..]);
    if rest.is_empty() {
        return Some((stem, 0));
    }
    let components: Vec<&str> = rest.strip_prefix('.')?.split('.').collect();
    components
        .iter()
        .all(|component| {
            !component.is_empty() && component.bytes().all(|byte| byte.is_ascii_digit())
        })
        .then_some((stem, components.len()))
}
