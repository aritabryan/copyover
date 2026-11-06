use puente_core::execute_json;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};

struct Fixture(Child);
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn fixture(rejected: bool) -> (Fixture, String) {
    fixture_with_mode(if rejected { Some("--reject") } else { None })
}

fn fixture_with_mode(mode: Option<&str>) -> (Fixture, String) {
    let path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/pairing_fixture.py");
    let mut command = Command::new("python");
    command.arg("-u").arg(path).stdout(Stdio::piped());
    if let Some(argument) = mode {
        command.arg(argument);
    }
    let mut child = command.spawn().unwrap();
    let mut port = String::new();
    BufReader::new(child.stdout.take().unwrap())
        .read_line(&mut port)
        .unwrap();
    let _: u16 = port.trim().parse().expect("Python fixture did not start");
    (Fixture(child), format!("http://127.0.0.1:{}", port.trim()))
}

#[test]
fn receives_cached_update_from_real_python_with_matching_hash() {
    use sha2::{Digest, Sha256};
    let (_process, server) = fixture_with_mode(Some("--updates"));
    let result = run(json!({"op":"update_check","server":server,"key":"fixture-only-key"}));
    assert_eq!(result["ok"], true, "{result}");
    let manifest = &result["update"];
    assert_eq!(manifest["versionCode"], 4);
    let folder = tempfile::tempdir().unwrap();
    let download = run(
        json!({"op":"update_download","server":server,"key":"fixture-only-key",
        "sha256":manifest["sha256"],"output_dir":folder.path()}),
    );
    assert_eq!(download["kind"], "file", "{download}");
    let bytes = std::fs::read(download["path"].as_str().unwrap()).unwrap();
    assert_eq!(bytes.len() as u64, manifest["size"].as_u64().unwrap());
    assert_eq!(
        format!("{:x}", Sha256::digest(&bytes)),
        manifest["sha256"].as_str().unwrap()
    );
    let denied = run(json!({"op":"update_check","server":server,"key":"wrong"}));
    assert_eq!(denied["code"], "unauthorized");
}

#[test]
fn receives_windows_clipboard_from_real_python_without_android_send() {
    let (_process, server) = fixture_with_mode(Some("--sync"));
    let directory = tempfile::tempdir().unwrap();
    let response = run(
        json!({"op":"sync_receive","server":server,"key":"fixture-only-key",
        "since":"","output_dir":directory.path()}),
    );
    assert_eq!(response["ok"], true, "{response}");
    assert_eq!(response["text"], "Windows → Android 👋");
    assert!(response["cursor"].as_str().unwrap().ends_with(":5"));
    let denied = run(json!({"op":"sync_receive","server":server,"key":"wrong",
        "since":"","output_dir":directory.path()}));
    assert_eq!(denied["code"], "unauthorized");
}

fn run(request: Value) -> Value {
    serde_json::from_str(&execute_json(&request.to_string())).unwrap()
}

#[test]
fn pairs_with_real_python_after_approval_without_plaintext_key() {
    let (_process, server) = fixture(false);
    let start = run(
        json!({"op":"pair_start","server":server,"name":"Android test","pc_id":"a".repeat(32)}),
    );
    assert_eq!(start["ok"], true, "{start}");
    let ticket = start["ticket"].clone();
    assert_eq!(ticket["code"].as_str().unwrap().len(), 6);
    let mut result = Value::Null;
    for _ in 0..20 {
        result = run(json!({"op":"pair_poll","server":server,"ticket":ticket}));
        if result["kind"] != "pair_pending" {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    assert_eq!(result["kind"], "pair_connection", "{result}");
    assert_eq!(result["connection"]["key"], "fixture-only-key");
    assert_eq!(result["connection"]["id"], "a".repeat(32));
}

#[test]
fn rejected_pairing_does_not_release_configuration() {
    let (_process, server) = fixture(true);
    let start = run(
        json!({"op":"pair_start","server":server,"name":"Android test","pc_id":"a".repeat(32)}),
    );
    assert_eq!(start["ok"], true, "{start}");
    let mut result = Value::Null;
    for _ in 0..20 {
        result = run(json!({"op":"pair_poll","server":server,"ticket":start["ticket"]}));
        if result["kind"] != "pair_pending" {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    assert_eq!(result["code"], "pair_rejected");
    assert!(result.get("connection").is_none());
}

fn pair(server: &str) -> Value {
    let start = run(
        json!({"op":"pair_start","server":server,"name":"Android test","pc_id":"a".repeat(32)}),
    );
    assert_eq!(start["ok"], true, "{start}");
    for _ in 0..50 {
        let result = run(json!({"op":"pair_poll","server":server,"ticket":start["ticket"]}));
        if result["kind"] != "pair_pending" {
            assert_eq!(result["kind"], "pair_connection", "{result}");
            return result["connection"].clone();
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    panic!("pairing did not finish");
}

#[test]
fn pairing_delivers_the_pc_ca_and_https_trusts_only_that_ca() {
    use rustls::pki_types::pem::PemObject;
    use sha2::{Digest, Sha256};
    let (_process, server) = fixture_with_mode(Some("--tls"));
    let connection = pair(&server);
    let ca = connection["ca"].as_str().expect("the PC sends its CA");
    assert!(ca.starts_with("-----BEGIN CERTIFICATE-----"));
    let der = rustls::pki_types::CertificateDer::from_pem_slice(ca.as_bytes()).unwrap();
    assert_eq!(
        connection["ca_sha256"].as_str().unwrap(),
        format!("{:x}", Sha256::digest(der.as_ref()))
    );
    // The QR contents stay compatible: the address an older app reads is still plain HTTP.
    assert!(connection["server"]
        .as_str()
        .unwrap()
        .starts_with("http://"));

    let https = server.replacen("http://", "https://", 1);
    // The phone's own key, not the PC's shared one: /identidad has to prove the PC with it.
    let key = connection["key"].as_str().unwrap();
    assert_ne!(key, "fixture-only-key");
    let ping = run(json!({"op":"ping","server":https,"key":key,"ca":ca}));
    assert_eq!(ping["ok"], true, "{ping}");
    let identity = run(
        json!({"op":"verify_identity","server":https,"key":key,"ca":ca,"pc_id":"a".repeat(32)}),
    );
    assert_eq!(identity["ok"], true, "{identity}");
    let denied = run(json!({"op":"verify_identity","server":https,"key":"unregistered-key",
        "ca":ca,"pc_id":"a".repeat(32)}));
    assert_eq!(denied["ok"], false, "{denied}");
    assert!(denied.get("server").is_none(), "{denied}");
    assert!(identity["server"]
        .as_str()
        .unwrap()
        .starts_with("https://127.0.0.1:"));

    // Another PC's CA: the handshake fails before the key is sent.
    let other = include_str!("fixtures/otra_ca.pem");
    let impostor = run(json!({"op":"ping","server":https,"key":key,"ca":other}));
    assert_eq!(impostor["code"], "certificate", "{impostor}");
    // With a pinned CA, plain HTTP is refused; HTTPS without one too.
    assert_eq!(
        run(json!({"op":"ping","server":server,"key":key,"ca":ca}))["code"],
        "config"
    );
    assert_eq!(
        run(json!({"op":"ping","server":https,"key":key}))["code"],
        "config"
    );
    // A link from before keeps working over HTTP on the same port.
    assert_eq!(
        run(json!({"op":"ping","server":server,"key":key}))["ok"],
        true
    );
}
