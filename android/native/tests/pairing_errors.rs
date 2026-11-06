use puente_core::execute_json;
use serde_json::{json, Value};
use std::net::TcpListener;

#[test]
fn pairing_network_error_identifies_stage_without_exposing_keys() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    drop(listener);
    let result: Value = serde_json::from_str(&execute_json(
        &json!({
            "op":"pair_start", "server":format!("http://{address}"),
            "pc_id":"a".repeat(32), "name":"Phone", "key":"secret-not-for-errors"
        })
        .to_string(),
    ))
    .unwrap();
    assert_eq!(result["code"], "network");
    let message = result["message"].as_str().unwrap();
    assert!(message.contains("Starting the link"), "{message}");
    assert!(!message.contains("secret-not-for-errors"));
}
