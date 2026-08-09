fn main() {
    generate_tray_translations();

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

/// Generate tray menu translations from frontend locale files.
fn generate_tray_translations() {
    use std::collections::BTreeMap;
    use std::fs;
    use std::path::Path;

    let out_dir = std::env::var("OUT_DIR").unwrap();
    let locales_dir = Path::new("../src/i18n/locales");

    println!("cargo:rerun-if-changed=../src/i18n/locales");

    let mut translations: BTreeMap<String, serde_json::Value> = BTreeMap::new();
    for entry in fs::read_dir(locales_dir).unwrap().flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }

        let language = path.file_name().unwrap().to_str().unwrap().to_string();
        let json_path = path.join("translation.json");
        println!("cargo:rerun-if-changed={}", json_path.display());

        let parsed: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&json_path).unwrap()).unwrap();
        if let Some(tray) = parsed.get("tray").cloned() {
            translations.insert(language, tray);
        }
    }

    let english = translations.get("en").unwrap().as_object().unwrap();
    let fields: Vec<_> = english
        .keys()
        .map(|key| (camel_to_snake(key), key.clone()))
        .collect();

    let mut output = String::from(
        "// Auto-generated from src/i18n/locales/*/translation.json - do not edit\n\n",
    );
    output.push_str("#[derive(Debug, Clone)]\npub struct TrayStrings {\n");
    for (rust_field, _) in &fields {
        output.push_str(&format!("    pub {rust_field}: String,\n"));
    }
    output.push_str("}\n\n");
    output.push_str(
        "pub static TRANSLATIONS: Lazy<HashMap<&'static str, TrayStrings>> = Lazy::new(|| {\n",
    );
    output.push_str("    let mut m = HashMap::new();\n");

    for (language, tray) in &translations {
        output.push_str(&format!("    m.insert(\"{language}\", TrayStrings {{\n"));
        for (rust_field, json_key) in &fields {
            let value = tray
                .get(json_key)
                .and_then(|value| value.as_str())
                .unwrap_or("");
            output.push_str(&format!(
                "        {rust_field}: \"{}\".to_string(),\n",
                escape_string(value)
            ));
        }
        output.push_str("    });\n");
    }

    output.push_str("    m\n});\n");
    fs::write(Path::new(&out_dir).join("tray_translations.rs"), output).unwrap();
    println!(
        "cargo:warning=Generated tray translations: {} languages, {} fields",
        translations.len(),
        fields.len()
    );
}

fn camel_to_snake(value: &str) -> String {
    value
        .chars()
        .enumerate()
        .fold(String::new(), |mut output, (index, character)| {
            if character.is_uppercase() && index > 0 {
                output.push('_');
            }
            output.push(character.to_lowercase().next().unwrap());
            output
        })
}

fn escape_string(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
}
