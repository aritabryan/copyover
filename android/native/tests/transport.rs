use puente_core::execute_json;
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;
use tempfile::tempdir;

#[test]
fn distinguishes_link_rejection_from_temporary_error() {
    let (url, server) = serve("403 Forbidden", "", b"");
    let result = run(json!({"op":"verify_identity","server":url,"key":"test-key","pc_id":"a".repeat(32)}));
    assert_eq!(result["ok"], false);
    assert_eq!(result["code"], "forbidden");
    let request = String::from_utf8(server.join().unwrap()).unwrap().to_lowercase();
    assert!(!request.contains("x-key:"));
}

fn serve(status: &str, headers: &str, body: &[u8]) -> (String, thread::JoinHandle<Vec<u8>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Length: {}\r\n{headers}Connection: close\r\n\r\n",
        body.len()
    );
    let body = body.to_vec();
    let handle = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(5)))
            .unwrap();
        let request = read_request(&mut stream);
        stream.write_all(response.as_bytes()).unwrap();
        stream.write_all(&body).unwrap();
        request
    });
    (url, handle)
}

fn read_request(stream: &mut TcpStream) -> Vec<u8> {
    let mut request = Vec::new();
    let mut byte = [0];
    while !request.ends_with(b"\r\n\r\n") {
        stream.read_exact(&mut byte).unwrap();
        request.push(byte[0]);
    }
    let head = String::from_utf8_lossy(&request).to_lowercase();
    let length: usize = head
        .lines()
        .find_map(|l| l.strip_prefix("content-length: "))
        .map(|s| s.trim().parse().unwrap())
        .unwrap_or(0);
    let start = request.len();
    request.resize(start + length, 0);
    stream.read_exact(&mut request[start..]).unwrap();
    request
}

#[test]
fn ping_checks_key_without_reading_or_changing_windows_clipboard() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let server = thread::spawn(move || {
        let replies = [
            "HTTP/1.1 200 OK\r\nContent-Length: 13\r\nConnection: close\r\n\r\nPuente activo",
            "HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        ];
        let mut requests = Vec::new();
        for reply in replies {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(std::time::Duration::from_secs(5)))
                .unwrap();
            requests.push(String::from_utf8(read_request(&mut stream)).unwrap());
            stream.write_all(reply.as_bytes()).unwrap();
        }
        requests
    });
    let result = run(json!({"op":"ping","server":url,"key":"abc"}));
    assert_eq!(result["ok"], true, "{result}");
    let requests = server.join().unwrap();
    assert!(requests[0].starts_with("GET /ping HTTP/1.1"));
    assert!(requests[1].starts_with("POST /send HTTP/1.1"));
    assert!(requests[1].to_lowercase().contains("x-key: abc"));
    assert!(requests[1].to_lowercase().contains("content-length: 0"));
}

fn run(value: Value) -> Value {
    serde_json::from_str(&execute_json(&value.to_string())).unwrap()
}

#[test]
fn automatic_receive_returns_content_and_cursor_without_sending_android_clipboard() {
    let token = format!("{}:7", "a".repeat(32));
    let headers =
        format!("Content-Type: text/plain; charset=utf-8\r\nX-Puente-Version: {token}\r\n");
    let (url, server) = serve("200 OK", &headers, b"from PC");
    let directory = tempdir().unwrap();
    let result = run(
        json!({"op":"sync_receive","server":url,"key":"abc","since":"","output_dir":directory.path()}),
    );
    assert_eq!(result["ok"], true, "{result}");
    assert_eq!(result["text"], "from PC");
    assert_eq!(result["cursor"], token);
    let request = String::from_utf8(server.join().unwrap()).unwrap();
    assert!(request.starts_with("GET /sincronizar?desde= HTTP/1.1"));
    assert!(request.to_lowercase().contains("x-key: abc"));
}

#[test]
fn automatic_receive_preserves_cursor_on_timeout_and_rejects_bad_tokens() {
    let token = format!("{}:8", "b".repeat(32));
    let (url, server) = serve(
        "204 No Content",
        &format!("X-Puente-Version: {token}\r\n"),
        b"",
    );
    let directory = tempdir().unwrap();
    let result = run(
        json!({"op":"sync_receive","server":url,"key":"abc","since":token,"output_dir":directory.path()}),
    );
    assert_eq!(result["ok"], true, "{result}");
    assert_eq!(result["kind"], "empty");
    assert_eq!(result["cursor"], token);
    server.join().unwrap();
    let bad = run(
        json!({"op":"sync_receive","server":"http://localhost:1","key":"abc","since":"bad\r\n","output_dir":directory.path()}),
    );
    assert_eq!(bad["code"], "protocol");
}

#[test]
fn update_manifest_uses_authenticated_endpoint() {
    let (url, server) = serve(
        "200 OK",
        "Content-Type: application/json\r\n",
        br#"{"update":null,"message":"No release"}"#,
    );
    let result = run(json!({"op":"update_check","server":url,"key":"abc"}));
    assert_eq!(result["ok"], true, "{result}");
    assert!(result["update"].is_null());
    let request = String::from_utf8(server.join().unwrap()).unwrap();
    assert!(request.starts_with("GET /android/actualizacion HTTP/1.1"));
    assert!(request.to_lowercase().contains("x-key: abc"));
}

#[test]
fn update_apk_uses_content_address_and_private_output() {
    let (url, server) = serve(
        "200 OK",
        "Content-Type: application/vnd.android.package-archive\r\n",
        b"PK-apk",
    );
    let dir = tempdir().unwrap();
    let digest = "a".repeat(64);
    let result = run(
        json!({"op":"update_download","server":url,"key":"abc","sha256":digest,"output_dir":dir.path()}),
    );
    assert_eq!(result["ok"], true, "{result}");
    assert_eq!(
        std::fs::read(result["path"].as_str().unwrap()).unwrap(),
        b"PK-apk"
    );
    let request = String::from_utf8(server.join().unwrap()).unwrap();
    assert!(request.starts_with(&format!("GET /android/apk/{digest} HTTP/1.1")));
}

#[test]
fn validates_configuration_without_connecting() {
    for server in [
        "https://pc:8765",
        "http://user:pass@pc",
        "http://pc/send",
        "http://pc?clave=x",
    ] {
        assert_eq!(
            run(json!({"op":"ping", "server":server, "key":"abc"}))["ok"],
            false
        );
    }
    assert_eq!(
        run(json!({"op":"ping", "server":"http://pc", "key":""}))["ok"],
        false
    );
}

#[test]
fn impostor_cannot_get_the_key_from_discovery_probe() {
    let (url, server) = serve(
        "200 OK",
        "Content-Type: application/json\r\n",
        br#"{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","address":"127.0.0.1","proof":"fake"}"#,
    );
    let result =
        run(json!({"op":"verify_identity","server":url,"key":"secret-key","pc_id":"a".repeat(32)}));
    assert_eq!(result["ok"], false);
    assert_eq!(result["code"], "identity");
    let request = String::from_utf8(server.join().unwrap()).unwrap();
    assert!(request.starts_with("GET /identidad?reto="));
    assert!(!request.to_lowercase().contains("x-key"));
    assert!(!request.contains("secret-key"));
}

#[test]
fn verifies_fresh_proof_and_rejects_proof_relayed_from_another_address() {
    use hmac::{Hmac, Mac};
    use sha2::Sha256;
    for (bound_address, accepted) in [("127.0.0.1", true), ("192.168.99.99", false)] {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://localhost:{}", listener.local_addr().unwrap().port());
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let request = String::from_utf8(read_request(&mut stream)).unwrap();
            assert!(!request.to_lowercase().contains("x-key"));
            let path = request.split_whitespace().nth(1).unwrap();
            let query = path.strip_prefix("/identidad?reto=").unwrap();
            let (nonce, fingerprint) = query.split_once("&huella=").unwrap();
            assert_eq!((nonce.len(), fingerprint.len()), (64, 32));
            // Signed with the shared key, as a PC from before the fingerprint does.
            let mut signer = Hmac::<Sha256>::new_from_slice(b"secret-key").unwrap();
            signer
                .update(format!("puente-v1:{}:{nonce}:{bound_address}", "a".repeat(32)).as_bytes());
            let proof: String = signer
                .finalize()
                .into_bytes()
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
            let body =
                json!({"id":"a".repeat(32),"address":bound_address,"proof":proof}).to_string();
            write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
        });
        let result = run(
            json!({"op":"verify_identity","server":url,"key":"secret-key","pc_id":"a".repeat(32)}),
        );
        assert_eq!(result["ok"], accepted, "{result}");
        if accepted {
            assert!(result["server"]
                .as_str()
                .unwrap_or("")
                .starts_with("http://127.0.0.1:"));
        }
        server.join().unwrap();
    }
}

#[test]
fn sends_unicode_text_with_existing_header() {
    let (url, server) = serve(
        "200 OK",
        "Content-Type: text/plain\r\n",
        b"Texto copiado en la PC",
    );
    let result = run(json!({"op":"send_text", "server":url, "key":"abc", "text":"Hola 👋"}));
    assert_eq!(result["ok"], true);
    let request = String::from_utf8(server.join().unwrap()).unwrap();
    assert!(request.starts_with("POST /send HTTP/1.1"));
    assert!(request.to_lowercase().contains("x-key: abc"));
    assert!(request.ends_with("Hola 👋"));
}

#[test]
fn multipart_keeps_file_name_mime_and_bytes() {
    let temp = tempdir().unwrap();
    let path = temp.path().join("upload");
    std::fs::write(&path, b"%PDF-android").unwrap();
    let (url, server) = serve("200 OK", "", b"Guardado");
    let result = run(
        json!({"op":"send_files", "server":url, "key":"abc", "files":[
            {"path":path, "name":"informe.pdf", "mime":"application/pdf"}
        ]}),
    );
    assert_eq!(result["ok"], true, "{result}");
    let request = String::from_utf8(server.join().unwrap()).unwrap();
    assert!(request.contains("filename=\"informe.pdf\""));
    assert!(request.contains("Content-Type: application/pdf\r\n\r\n%PDF-android"));
    assert!(!request
        .to_lowercase()
        .contains("transfer-encoding: chunked"));
}

#[test]
fn shared_txt_file_does_not_trigger_ios_clipboard_text_mode() {
    let temp = tempdir().unwrap();
    let path = temp.path().join("notas.txt");
    std::fs::write(&path, b"notas").unwrap();
    let (url, server) = serve("200 OK", "", b"Guardado");
    let result = run(
        json!({"op":"send_files", "server":url, "key":"abc", "files":[
            {"path":path, "name":"notas.txt", "mime":"text/plain"}
        ]}),
    );
    assert_eq!(result["ok"], true);
    let request = String::from_utf8(server.join().unwrap()).unwrap();
    assert!(request.contains("Content-Type: application/octet-stream\r\n"));
}

#[test]
fn receives_text_and_empty_clipboard() {
    for (status, body, kind) in [
        ("200 OK", "Desde PC 👋", "text"),
        ("204 No Content", "", "empty"),
    ] {
        let temp = tempdir().unwrap();
        let (url, server) = serve(
            status,
            "Content-Type: text/plain; charset=utf-8\r\n",
            body.as_bytes(),
        );
        let result =
            run(json!({"op":"receive", "server":url, "key":"abc", "output_dir":temp.path()}));
        assert_eq!(result["kind"], kind);
        if kind == "text" {
            assert_eq!(result["text"], body);
        }
        server.join().unwrap();
    }
}

#[test]
fn named_text_file_is_a_file_and_unicode_name_is_decoded() {
    let temp = tempdir().unwrap();
    let (url, server) = serve("200 OK", "Content-Type: text/plain\r\nContent-Disposition: attachment; filename*=UTF-8''espa%C3%B1ol.txt\r\n", b"contenido");
    let result = run(json!({"op":"receive", "server":url, "key":"abc", "output_dir":temp.path()}));
    assert_eq!(result["kind"], "file");
    assert_eq!(result["name"], "español.txt");
    assert_eq!(
        std::fs::read(result["path"].as_str().unwrap()).unwrap(),
        b"contenido"
    );
    server.join().unwrap();
}

#[test]
fn reports_wrong_key_and_does_not_follow_redirects() {
    for (status, code) in [
        ("401 Unauthorized", "unauthorized"),
        ("302 Found", "redirect"),
    ] {
        let (url, server) = serve(status, "Location: http://127.0.0.1:1/other\r\n", b"");
        let result = run(json!({"op":"ping", "server":url, "key":"abc"}));
        assert_eq!(result["ok"], false);
        assert_eq!(result["code"], code);
        server.join().unwrap();
    }
}

#[test]
fn rejects_empty_send_and_oversized_upload_before_connecting() {
    let result = run(json!({"op":"send_text", "server":"http://pc", "key":"abc", "text":""}));
    assert_eq!(result["code"], "empty");
    // A sparse file verifies the upload limit without allocating 500 MiB.
    let temp = tempdir().unwrap();
    let path = temp.path().join("huge");
    std::fs::File::create(&path)
        .unwrap()
        .set_len(500 * 1024 * 1024)
        .unwrap();
    let result = run(
        json!({"op":"send_files", "server":"http://pc", "key":"abc", "files":[
            {"path":path, "name":"huge.bin", "mime":"application/octet-stream"}
        ]}),
    );
    assert_eq!(result["code"], "too_large");
}
