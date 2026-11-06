use crate::{response::read_small, Failure};
use hmac::{Hmac, Mac};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::net::{IpAddr, ToSocketAddrs};

#[derive(Deserialize)]
struct Proof {
    id: String,
    address: String,
    proof: String,
}

fn rejected() -> Failure {
    Failure::new(
        "identity",
        "Couldn't verify your PC. Restart CopyOver on the PC or link it again.",
    )
}

pub(crate) fn verify(
    agent: &ureq::Agent,
    server: &str,
    key: &str,
    pc_id: &str,
) -> Result<Value, Failure> {
    let mut last_error = rejected();
    for address in literal_addresses(server)? {
        match verify_at(agent, &address, key, pc_id) {
            Ok(value) => return Ok(value),
            Err(error) => last_error = error,
        }
    }
    Err(last_error)
}

fn verify_at(agent: &ureq::Agent, server: &str, key: &str, pc_id: &str) -> Result<Value, Failure> {
    let mut random = [0_u8; 32];
    getrandom::getrandom(&mut random).map_err(|_| rejected())?;
    let nonce: String = random.iter().map(|b| format!("{b:02x}")).collect();
    // The PC only keeps the hash of this phone's own key, so it signs with that
    // hash; the fingerprint tells it which one without sending the key.
    let key_hash = hex(&Sha256::digest(key.as_bytes()));
    let fingerprint = &hex(&Sha256::digest(format!("puente-huella:{key_hash}").as_bytes()))[..32];
    // This request never contains X-Key. A public mDNS id alone is not proof
    // that a discovered address (or a reassigned old IP) belongs to the PC.
    let response = crate::checked(
        agent
            .get(&format!("{server}/identidad?reto={nonce}&huella={fingerprint}"))
            .call(),
    )?;
    let body = read_small(response, 4096)?;
    let proof: Proof = serde_json::from_str(&body).map_err(|_| rejected())?;
    if proof.id != pc_id || proof.proof.len() != 64 {
        return Err(rejected());
    }
    let signature = decode_hex(&proof.proof)?;
    let message = format!("puente-v1:{pc_id}:{nonce}:{}", proof.address);
    // A PC from before the fingerprint ignores it and signs with the shared key.
    if !signed_with(&key_hash, &message, &signature) && !signed_with(key, &message, &signature) {
        return Err(rejected());
    }
    verify_address(server, &proof.address)?;
    Ok(json!({"ok":true,"kind":"address","server":server,"message":"PC verified"}))
}

fn signed_with(secret: &str, message: &str, signature: &[u8]) -> bool {
    let Ok(mut verifier) = Hmac::<Sha256>::new_from_slice(secret.as_bytes()) else {
        return false;
    };
    verifier.update(message.as_bytes());
    verifier.verify_slice(signature).is_ok()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn decode_hex(value: &str) -> Result<Vec<u8>, Failure> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let text = std::str::from_utf8(pair).map_err(|_| rejected())?;
            u8::from_str_radix(text, 16).map_err(|_| rejected())
        })
        .collect()
}

fn verify_address(server: &str, bound_address: &str) -> Result<(), Failure> {
    // Include the server's actual local socket address in its proof. This also
    // rejects a different host relaying a proof made for the genuine PC's IP.
    let expected: IpAddr = bound_address.parse().map_err(|_| rejected())?;
    let url = url::Url::parse(server).map_err(|_| rejected())?;
    let host = url
        .host_str()
        .ok_or_else(rejected)?
        .trim_matches(['[', ']']);
    let connected: IpAddr = host.parse().map_err(|_| rejected())?;
    if connected != expected {
        return Err(rejected());
    }
    Ok(())
}

fn literal_addresses(server: &str) -> Result<Vec<String>, Failure> {
    let url = url::Url::parse(server).map_err(|_| rejected())?;
    let host = url
        .host_str()
        .ok_or_else(rejected)?
        .trim_matches(['[', ']']);
    let port = url.port_or_known_default().unwrap_or(80);
    let scheme = url.scheme();
    // Resolve once. Proof, authenticated ping and transfer all use the same
    // literal destination; a second DNS result must not broaden the identity.
    let addresses = (host, port).to_socket_addrs().map_err(|_| rejected())?;
    let mut result = Vec::new();
    for address in addresses.take(8) {
        let server = format!("{scheme}://{address}");
        if !result.contains(&server) {
            result.push(server);
        }
    }
    Ok(result)
}
