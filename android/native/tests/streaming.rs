use puente_core::{execute_json, execute_tracked_json, transfer_status_json};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    thread,
};
// Tracked operations discard finished entries when a new operation starts.
// Serialize these tests so progress assertions observe their own retained entry.
static SERIAL: std::sync::Mutex<()> = std::sync::Mutex::new(());
#[test]
fn unavailable_offer_is_terminal_instead_of_busy_forever() {
    let _serial = SERIAL.lock().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let server = thread::spawn(move || {
        let (mut socket, _) = listener.accept().unwrap();
        request(&mut socket);
        reply(&mut socket, 409, "", b"unavailable source");
    });
    let (done, completed) = std::sync::mpsc::channel();
    let worker = thread::spawn(move || {
        let result: Value = serde_json::from_str(&execute_tracked_json(
            "unavailable-offer",
            &json!({
                "op":"offer_check", "server":format!("http://{addr}"), "key":"secret"
            })
            .to_string(),
        ))
        .unwrap();
        done.send(()).unwrap();
        result
    });
    let timely = completed
        .recv_timeout(std::time::Duration::from_millis(1500))
        .is_ok();
    if !timely {
        puente_core::cancel_transfer("unavailable-offer");
    }
    let result = worker.join().unwrap();
    server.join().unwrap();
    assert!(
        timely,
        "An unavailable source should report an error promptly"
    );
    assert_eq!(result["code"], "server");
}
fn hash(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}
fn request(socket: &mut TcpStream) -> String {
    socket
        .set_read_timeout(Some(std::time::Duration::from_secs(5)))
        .unwrap();
    let mut bytes = Vec::new();
    while !bytes.ends_with(b"\r\n\r\n") {
        let mut b = [0];
        socket.read_exact(&mut b).unwrap();
        bytes.push(b[0]);
    }
    String::from_utf8(bytes).unwrap()
}
fn reply(socket: &mut TcpStream, status: u16, headers: &str, body: &[u8]) {
    write!(
        socket,
        "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n{headers}\r\n",
        body.len()
    )
    .unwrap();
    socket.write_all(body).unwrap();
}
fn download_case(mode: &str) -> Value {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let mode = mode.to_owned();
    let transfer_id = format!("stream-download-{mode}");
    let worker = thread::spawn(move || {
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        assert!(h.contains("X-Key: secret"));
        if mode == "corrupt" {
            reply(&mut s, 200, "", b"bad!");
            return;
        }
        s.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\nda")
            .unwrap();
        drop(s);
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        assert!(h.contains("Range: bytes=2-"));
        match mode.as_str() {
            "ignore" => reply(&mut s, 200, "", b"data"),
            "mismatch" => reply(&mut s, 206, "Content-Range: bytes 1-2/4\r\n", b"ta"),
            _ => reply(&mut s, 206, "Content-Range: bytes 2-3/4\r\n", b"ta"),
        }
    });
    let dir = tempfile::tempdir().unwrap();
    let result:Value=serde_json::from_str(&execute_tracked_json(&transfer_id,&json!({"op":"offer_download","id":"offer","server":format!("http://{addr}"),"key":"secret","size":4,"sha256":hash(b"data"),"output_dir":dir.path()}).to_string())).unwrap();
    worker.join().unwrap();
    if result["ok"] == true {
        assert_eq!(
            std::fs::read(dir.path().join("contenido")).unwrap(),
            b"data"
        );
    } else {
        assert!(!dir.path().join("contenido").exists());
    }
    result
}
#[test]
fn download_resumes_and_progress_excludes_retries() {
    let _serial = SERIAL.lock().unwrap();
    assert_eq!(download_case("resume")["ok"], true);
    let status: Value =
        serde_json::from_str(&transfer_status_json("stream-download-resume")).unwrap();
    assert_eq!(status["bytes"], 4);
    assert_eq!(status["total"], 4);
}
#[test]
fn download_rejects_corrupted_hash() {
    let _serial = SERIAL.lock().unwrap();
    assert_eq!(download_case("corrupt")["code"], "hash");
}
#[test]
fn download_rejects_ignored_range() {
    let _serial = SERIAL.lock().unwrap();
    assert_eq!(download_case("ignore")["code"], "protocol");
}
#[test]
fn download_rejects_mismatched_range() {
    let _serial = SERIAL.lock().unwrap();
    assert_eq!(download_case("mismatch")["code"], "protocol");
}
#[test]
fn upload_requires_new_pc_capability() {
    let _serial = SERIAL.lock().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let worker = thread::spawn(move || {
        let (mut s, _) = listener.accept().unwrap();
        assert!(request(&mut s).starts_with("GET /android/transferencias "));
        reply(&mut s, 404, "", b"missing");
    });
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("a");
    std::fs::write(&path, b"data").unwrap();
    let result:Value=serde_json::from_str(&execute_json(&json!({"op":"send_stream_files","server":format!("http://{addr}"),"key":"secret","files":[{"path":path,"name":"a","mime":"text/plain"}]}).to_string())).unwrap();
    assert_eq!(result["code"], "update_required");
    worker.join().unwrap();
}
fn body(socket: &mut TcpStream, headers: &str) -> Vec<u8> {
    let length = headers
        .lines()
        .find_map(|l| {
            l.to_ascii_lowercase()
                .strip_prefix("content-length:")
                .map(|v| v.trim().parse::<usize>().unwrap())
        })
        .unwrap_or(0);
    let mut bytes = vec![0; length];
    socket.read_exact(&mut bytes).unwrap();
    bytes
}
#[test]
fn upload_resumes_from_receiver_offset_without_counting_metadata() {
    let _serial = SERIAL.lock().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let worker = thread::spawn(move || {
        for step in 0..8 {
            let (mut s, _) = listener.accept().unwrap();
            let h = request(&mut s);
            assert!(h.contains("X-Key: secret"));
            match step {
                0 => reply(&mut s, 200, "", br#"{"version":1}"#),
                1 => {
                    let data: Value = serde_json::from_slice(&body(&mut s, &h)).unwrap();
                    assert_eq!(data["files"][0]["sha256"], hash(b"data"));
                    reply(&mut s, 200, "", br#"{"id":"session","resume":true}"#);
                }
                2 => {
                    body(&mut s, &h);
                    reply(
                        &mut s,
                        200,
                        "",
                        br#"{"files":[{"offset":0,"received":false}]}"#,
                    );
                }
                3 => {
                    assert!(h.starts_with("PUT /android/transferencias/session/0 "));
                    assert_eq!(body(&mut s, &h), b"data");
                    reply(&mut s, 503, "", b"cut");
                }
                4 => {
                    body(&mut s, &h);
                    reply(&mut s, 409, "", b"hashing");
                }
                5 => {
                    body(&mut s, &h);
                    reply(
                        &mut s,
                        200,
                        "",
                        br#"{"files":[{"offset":2,"received":false}]}"#,
                    );
                }
                6 => {
                    assert!(h.contains("X-Puente-Offset: 2"));
                    assert_eq!(body(&mut s, &h), b"ta");
                    reply(&mut s, 200, "", b"{}");
                }
                _ => {
                    body(&mut s, &h);
                    reply(
                        &mut s,
                        200,
                        "",
                        br#"{"files":[{"offset":4,"received":true}]}"#,
                    );
                }
            }
        }
    });
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("a");
    std::fs::write(&path, b"data").unwrap();
    let result:Value=serde_json::from_str(&execute_tracked_json("stream-upload",&json!({"op":"send_stream_files","server":format!("http://{addr}"),"key":"secret","files":[{"path":path,"name":"a","mime":"text/plain"}]}).to_string())).unwrap();
    worker.join().unwrap();
    assert_eq!(result["ok"], true, "{result}");
    let status: Value = serde_json::from_str(&transfer_status_json("stream-upload")).unwrap();
    assert_eq!(status["bytes"], 4);
    assert_eq!(status["total"], 4);
}
#[test]
fn upload_metadata_accepts_file_above_legacy_cap() {
    let _serial = SERIAL.lock().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let size = puente_core::MAX_BYTES + 1;
    let worker = thread::spawn(move || {
        let (mut s, _) = listener.accept().unwrap();
        request(&mut s);
        reply(&mut s, 200, "", br#"{"version":1}"#);
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        let metadata: Value = serde_json::from_slice(&body(&mut s, &h)).unwrap();
        assert_eq!(metadata["files"][0]["size"], size);
        assert_eq!(metadata["archivos"][0]["tamano"], size);
        reply(&mut s, 200, "", br#"{"id":"large","resume":true}"#);
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        body(&mut s, &h);
        reply(
            &mut s,
            200,
            "",
            json!({"files":[{"offset":size,"received":true}]})
                .to_string()
                .as_bytes(),
        );
    });
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("large");
    std::fs::File::create(&path).unwrap().set_len(size).unwrap();
    let result:Value=serde_json::from_str(&execute_json(&json!({"op":"send_stream_files","server":format!("http://{addr}"),"key":"secret","files":[{"path":path,"name":"large","mime":"application/octet-stream"}]}).to_string())).unwrap();
    assert_eq!(result["ok"], true, "{result}");
    worker.join().unwrap();
}

#[test]
fn offer_check_retries_busy_snapshot_and_does_not_count_metadata() {
    let _serial = SERIAL.lock().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let worker = thread::spawn(move || {
        let (mut s, _) = listener.accept().unwrap();
        request(&mut s);
        reply(&mut s, 503, "", b"busy");
        let (mut s, _) = listener.accept().unwrap();
        request(&mut s);
        reply(&mut s, 200, "", br#"{"oferta":{"id":"a"}}"#);
    });
    let result: Value = serde_json::from_str(&execute_tracked_json(
        "offer-snapshot-retry",
        &json!({"op":"offer_check","server":format!("http://{addr}"),"key":"secret"}).to_string(),
    ))
    .unwrap();
    worker.join().unwrap();
    assert_eq!(result["offer"]["id"], "a");
    let status: Value =
        serde_json::from_str(&transfer_status_json("offer-snapshot-retry")).unwrap();
    assert_eq!(status["bytes"], 0);
}
#[test]
fn offer_check_legacy_404_allows_clipboard_fallback() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let worker = thread::spawn(move || {
        let (mut s, _) = listener.accept().unwrap();
        request(&mut s);
        reply(&mut s, 404, "", b"missing");
    });
    let result: Value = serde_json::from_str(&execute_json(
        &json!({"op":"offer_check","server":format!("http://{addr}"),"key":"secret"}).to_string(),
    ))
    .unwrap();
    worker.join().unwrap();
    assert_eq!(result["ok"], true);
    assert!(result["offer"].is_null());
}
#[test]
fn cancelled_reconnect_cancels_remote_session() {
    let _serial = SERIAL.lock().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let (signal, ready) = std::sync::mpsc::channel();
    let server = thread::spawn(move || {
        let (mut s, _) = listener.accept().unwrap();
        request(&mut s);
        reply(&mut s, 200, "", br#"{"version":1}"#);
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        body(&mut s, &h);
        reply(&mut s, 200, "", br#"{"id":"cancel","reanudar":true}"#);
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        body(&mut s, &h);
        reply(
            &mut s,
            200,
            "",
            br#"{"archivos":[{"offset":0,"recibido":false}]}"#,
        );
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        body(&mut s, &h);
        reply(&mut s, 503, "", b"cut");
        drop(s);
        signal.send(()).unwrap();
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        assert!(h.starts_with("POST /android/transferencias/cancel/cancelar "));
        reply(&mut s, 200, "", b"{}");
    });
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("a");
    std::fs::write(&path, b"data").unwrap();
    let command=json!({"op":"send_stream_files","server":format!("http://{addr}"),"key":"secret","files":[{"path":path,"name":"a","mime":"text/plain"}]}).to_string();
    let worker = thread::spawn(move || execute_tracked_json("cancel-stream-reconnect", &command));
    ready
        .recv_timeout(std::time::Duration::from_secs(5))
        .unwrap();
    puente_core::cancel_transfer("cancel-stream-reconnect");
    let started = std::time::Instant::now();
    let result: Value = serde_json::from_str(&worker.join().unwrap()).unwrap();
    server.join().unwrap();
    assert_eq!(result["code"], "cancelled");
    assert!(started.elapsed() < std::time::Duration::from_secs(2));
}
#[test]
fn cancelled_download_removes_partial_and_never_publishes() {
    let _serial = SERIAL.lock().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let (signal, ready) = std::sync::mpsc::channel();
    let server = thread::spawn(move || {
        let (mut s, _) = listener.accept().unwrap();
        request(&mut s);
        s.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\nda")
            .unwrap();
        drop(s);
        signal.send(()).unwrap();
    });
    let dir = tempfile::tempdir().unwrap();
    let command=json!({"op":"offer_download","id":"offer","server":format!("http://{addr}"),"key":"secret","size":4,"sha256":hash(b"data"),"output_dir":dir.path()}).to_string();
    let worker = thread::spawn(move || execute_tracked_json("cancel-stream-download", &command));
    ready
        .recv_timeout(std::time::Duration::from_secs(5))
        .unwrap();
    puente_core::cancel_transfer("cancel-stream-download");
    let result: Value = serde_json::from_str(&worker.join().unwrap()).unwrap();
    server.join().unwrap();
    assert_eq!(result["code"], "cancelled");
    assert!(!dir.path().join("contenido").exists());
    assert!(!dir.path().join("contenido.partial").exists());
}
#[test]
fn retries_capability_and_lost_create_response_with_identical_metadata() {
    let _serial = SERIAL.lock().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let server = thread::spawn(move || {
        let (mut s, _) = listener.accept().unwrap();
        request(&mut s);
        reply(&mut s, 503, "", b"busy");
        let (mut s, _) = listener.accept().unwrap();
        request(&mut s);
        reply(&mut s, 200, "", br#"{"version":1}"#);
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        let first = body(&mut s, &h);
        s.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 30\r\nConnection: close\r\n\r\n{")
            .unwrap();
        drop(s);
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        assert_eq!(body(&mut s, &h), first);
        reply(&mut s, 200, "", br#"{"id":"same","resume":true}"#);
        let (mut s, _) = listener.accept().unwrap();
        let h = request(&mut s);
        body(&mut s, &h);
        reply(
            &mut s,
            200,
            "",
            br#"{"files":[{"offset":4,"received":true}]}"#,
        );
    });
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("a");
    std::fs::write(&path, b"data").unwrap();
    let result:Value=serde_json::from_str(&execute_json(&json!({"op":"send_stream_files","server":format!("http://{addr}"),"key":"secret","files":[{"path":path,"name":"a","mime":"text/plain"}]}).to_string())).unwrap();
    server.join().unwrap();
    assert_eq!(result["ok"], true, "{result}");
}
#[test]
fn cancelling_hashing_does_not_count_hash_bytes_or_create_session() {
    let _serial = SERIAL.lock().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let (signal, ready) = std::sync::mpsc::channel();
    let server = thread::spawn(move || {
        let (mut s, _) = listener.accept().unwrap();
        request(&mut s);
        reply(&mut s, 200, "", br#"{"version":1}"#);
        signal.send(()).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(200));
        listener.set_nonblocking(true).unwrap();
        assert!(listener.accept().is_err());
    });
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("hash");
    std::fs::File::create(&path)
        .unwrap()
        .set_len(64 * 1024 * 1024)
        .unwrap();
    let command=json!({"op":"send_stream_files","server":format!("http://{addr}"),"key":"secret","files":[{"path":path,"name":"hash","mime":"application/octet-stream"}]}).to_string();
    let worker = thread::spawn(move || execute_tracked_json("cancel-stream-hash", &command));
    ready
        .recv_timeout(std::time::Duration::from_secs(5))
        .unwrap();
    puente_core::cancel_transfer("cancel-stream-hash");
    let result: Value = serde_json::from_str(&worker.join().unwrap()).unwrap();
    server.join().unwrap();
    assert_eq!(result["code"], "cancelled");
    let status: Value = serde_json::from_str(&transfer_status_json("cancel-stream-hash")).unwrap();
    assert_eq!(status["bytes"], 0);
}
#[test]
fn stream_capability_authentication_errors_are_terminal() {
    let _serial = SERIAL.lock().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let server = thread::spawn(move || {
        let (mut s, _) = listener.accept().unwrap();
        request(&mut s);
        reply(&mut s, 401, "", b"auth");
    });
    let result:Value=serde_json::from_str(&execute_json(&json!({"op":"send_stream_files","server":format!("http://{addr}"),"key":"secret","files":[{"path":"unused","name":"a","mime":"text/plain"}]}).to_string())).unwrap();
    server.join().unwrap();
    assert_eq!(result["code"], "unauthorized");
}
