use puente_core::{cancel_transfer, execute_tracked_json, transfer_status_json};
use serde_json::Value;
#[test]
fn cancellation_interrupts_continuously_trickling_clipboard_text() {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        thread,
        time::{Duration, Instant},
    };
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0; 4096];
        assert!(stream.read(&mut request).unwrap() > 0);
        stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 100\r\nConnection: close\r\n\r\n").unwrap();
        for _ in 0..100 {
            if stream.write_all(b"x").is_err() {
                break;
            }
            thread::sleep(Duration::from_millis(30));
        }
    });
    let directory = tempfile::tempdir().unwrap();
    let request=serde_json::json!({"op":"receive","server":format!("http://{address}"),"key":"secret","output_dir":directory.path()}).to_string();
    let worker = thread::spawn(move || execute_tracked_json("trickling-text", &request));
    thread::sleep(Duration::from_millis(150));
    let start = Instant::now();
    cancel_transfer("trickling-text");
    let result: Value = serde_json::from_str(&worker.join().unwrap()).unwrap();
    server.join().unwrap();
    assert_eq!(result["code"], "cancelled");
    assert!(start.elapsed() < Duration::from_secs(1));
}
#[test]
fn cancelled_operation_reports_cancelled_and_can_be_polled() {
    assert!(!cancel_transfer("missing"));
    let result: Value = serde_json::from_str(&execute_tracked_json("test-invalid", "{}")).unwrap();
    assert_eq!(result["ok"], false);
    let status: Value = serde_json::from_str(&transfer_status_json("test-invalid")).unwrap();
    assert_eq!(status["finished"], true);
}
#[test]
fn cancellation_closes_stalled_download_and_removes_partial_file() {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        thread,
        time::{Duration, Instant},
    };
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = thread::spawn(move || {
        let (mut socket, _) = listener.accept().unwrap();
        let mut request = [0; 4096];
        let count = socket.read(&mut request).unwrap();
        assert!(count > 0);
        socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\nContent-Type: application/octet-stream\r\nConnection: close\r\n\r\npartial").unwrap();
        thread::sleep(Duration::from_secs(5));
    });
    let directory = tempfile::tempdir().unwrap();
    let command=serde_json::json!({"op":"receive","server":format!("http://{address}"),"key":"secret","output_dir":directory.path()}).to_string();
    let transfer = thread::spawn(move || execute_tracked_json("stalled-download", &command));
    thread::sleep(Duration::from_millis(300));
    let start = Instant::now();
    assert!(cancel_transfer("stalled-download"));
    let result: Value = serde_json::from_str(&transfer.join().unwrap()).unwrap();
    assert_eq!(result["code"], "cancelled");
    assert!(start.elapsed() < Duration::from_secs(4));
    assert!(!directory.path().join("contenido").exists());
    server.join().unwrap();
}
#[test]
fn cancellation_before_execute_never_contacts_network() {
    cancel_transfer("before-start");
    let result: Value = serde_json::from_str(&execute_tracked_json(
        "before-start",
        r#"{"op":"receive","server":"http://127.0.0.1:1","key":"secret","output_dir":"unused"}"#,
    ))
    .unwrap();
    assert_eq!(result["code"], "cancelled");
}
#[test]
fn offers_use_authenticated_immutable_download_and_confirmation_paths() {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        thread,
    };
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let server = thread::spawn(move || {
        for (path, body, headers) in [
            (
                "/android/ofertas",
                r#"{"offer":{"id":"abc123","name":"a.txt","mime":"text/plain","size":4,"pc":"PC"}}"#,
                "Content-Type: application/json\r\n",
            ),
            (
                "/android/ofertas/abc123/archivo",
                "data",
                "Content-Type: text/plain\r\nContent-Disposition: attachment; filename=a.txt\r\n",
            ),
            (
                "/android/ofertas/abc123/confirmar",
                r#"{"ok":true}"#,
                "Content-Type: application/json\r\n",
            ),
        ] {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut byte = [0];
            while !request.ends_with(b"\r\n\r\n") {
                stream.read_exact(&mut byte).unwrap();
                request.push(byte[0]);
            }
            let request = String::from_utf8(request).unwrap();
            assert!(request.lines().next().unwrap().contains(path));
            assert!(request.contains("X-Key: secret"));
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n{headers}Connection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        }
    });
    let dir = tempfile::tempdir().unwrap();
    for op in ["offer_check", "offer_download", "offer_confirm"] {
        let request = serde_json::json!({"op":op,"server":format!("http://{address}"),"key":"secret","id":"abc123","output_dir":dir.path()});
        let result: Value =
            serde_json::from_str(&puente_core::execute_json(&request.to_string())).unwrap();
        assert_eq!(result["ok"], true, "{result}");
    }
    assert_eq!(
        std::fs::read(dir.path().join("contenido")).unwrap(),
        b"data"
    );
    server.join().unwrap();
}
