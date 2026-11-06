use crate::{response::read_small, Failure};
use aes_gcm::{
    aead::{Aead, Payload},
    Aes256Gcm, KeyInit, Nonce,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use hkdf::Hkdf;
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use x25519_dalek::{PublicKey, StaticSecret};

#[derive(Deserialize)]
pub(crate) struct Ticket {
    session: String,
    private_key: String,
    server_public: String,
    pc_id: String,
}

fn invalid() -> Failure {
    Failure::new("pairing", "Couldn't link this PC. Search for it again.")
}

fn checked_stage(
    stage: &str,
    result: Result<ureq::Response, ureq::Error>,
) -> Result<ureq::Response, Failure> {
    match result {
        // Pairing URLs contain no key or session token. Keep the transport cause
        // so a refused connection, timeout, or malformed response can be diagnosed.
        Err(ureq::Error::Transport(error)) => {
            Err(Failure::new("network", format!("{stage}: {error}")))
        }
        other => crate::checked(other).map_err(|mut error| {
            error.message = format!("{stage}: {}", error.message);
            error
        }),
    }
}

fn key_bytes(value: &str) -> Result<[u8; 32], Failure> {
    STANDARD
        .decode(value)
        .map_err(|_| invalid())?
        .try_into()
        .map_err(|_| invalid())
}

fn derive(private: &StaticSecret, public: &PublicKey) -> Result<[u8; 32], Failure> {
    let shared = private.diffie_hellman(public);
    if !shared.was_contributory() {
        return Err(invalid());
    }
    let mut key = [0_u8; 32];
    Hkdf::<Sha256>::new(None, shared.as_bytes())
        .expand(b"puente-pair-v1", &mut key)
        .map_err(|_| invalid())?;
    Ok(key)
}

pub(crate) fn start(
    agent: &ureq::Agent,
    server: &str,
    name: &str,
    pc_id: &str,
) -> Result<Value, Failure> {
    let mut bytes = [0_u8; 32];
    getrandom::getrandom(&mut bytes).map_err(|_| invalid())?;
    let private = StaticSecret::from(bytes);
    let public = PublicKey::from(&private);
    let commitment: String = Sha256::digest(public.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let body = json!({"name":name,"commitment":commitment}).to_string();
    let response = checked_stage(
        "Starting the link",
        agent
            .post(&format!("{server}/vincular"))
            .set("Content-Type", "application/json")
            .send_string(&body),
    )?;
    let response: Value =
        serde_json::from_str(&read_small(response, 4096)?).map_err(|_| invalid())?;
    if response["id"] != pc_id {
        return Err(invalid());
    }
    let session = response["session"].as_str().ok_or_else(invalid)?;
    validate_session(session)?;
    let server_public = response["server_public"].as_str().ok_or_else(invalid)?;
    let public = PublicKey::from(key_bytes(server_public)?);
    let key = derive(&private, &public)?;
    // The PC has fixed its public key and session before seeing our public
    // key. Reveal only now, so a relay cannot grind matching comparison codes.
    let reveal = json!({"session":session,"public_key":STANDARD.encode(PublicKey::from(&private).as_bytes())}).to_string();
    let response_reveal = checked_stage(
        "Exchanging the code",
        agent
            .post(&format!("{server}/vincular/codigo"))
            .set("Content-Type", "application/json")
            .send_string(&reveal),
    )?;
    let _: Value =
        serde_json::from_str(&read_small(response_reveal, 1024)?).map_err(|_| invalid())?;
    let mut hash = Sha256::new();
    hash.update(key);
    hash.update(session.as_bytes());
    let digest = hash.finalize();
    let number = u32::from_be_bytes(digest[..4].try_into().map_err(|_| invalid())?) % 1_000_000;
    Ok(json!({"ok":true,"kind":"pair_ticket","ticket":{
        "session":session,"private_key":STANDARD.encode(private.to_bytes()),"server_public":server_public,
        "pc_id":pc_id,"code":format!("{number:06}"),"server":server
    }}))
}

fn validate_session(session: &str) -> Result<(), Failure> {
    if session.len() != 32
        || !session
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        return Err(invalid());
    }
    Ok(())
}

pub(crate) fn poll(agent: &ureq::Agent, server: &str, ticket: Ticket) -> Result<Value, Failure> {
    validate_session(&ticket.session)?;
    let response = checked_stage(
        "PC confirmation",
        agent
            .get(&format!("{server}/vincular/estado"))
            .set("X-Vinculo", &ticket.session)
            .call(),
    )?;
    let response: Value =
        serde_json::from_str(&read_small(response, 8192)?).map_err(|_| invalid())?;
    match response["status"].as_str() {
        Some("pending") => Ok(json!({"ok":true,"kind":"pair_pending"})),
        Some("approved") => decrypt(response, ticket),
        Some("rejected") => Err(Failure::new("pair_rejected", "The PC declined the connection.")),
        Some("expired") => Err(Failure::new(
            "pair_expired",
            "The request expired. Try again.",
        )),
        _ => Err(invalid()),
    }
}

fn decrypt(response: Value, ticket: Ticket) -> Result<Value, Failure> {
    let private = StaticSecret::from(key_bytes(&ticket.private_key)?);
    let public = PublicKey::from(key_bytes(&ticket.server_public)?);
    let key = derive(&private, &public)?;
    let cipher = Aes256Gcm::new_from_slice(&key).map_err(|_| invalid())?;
    let nonce = STANDARD
        .decode(response["nonce"].as_str().ok_or_else(invalid)?)
        .map_err(|_| invalid())?;
    if nonce.len() != 12 {
        return Err(invalid());
    }
    let encrypted = STANDARD
        .decode(response["ciphertext"].as_str().ok_or_else(invalid)?)
        .map_err(|_| invalid())?;
    let plaintext = cipher
        .decrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: &encrypted,
                aad: ticket.session.as_bytes(),
            },
        )
        .map_err(|_| invalid())?;
    let connection: Value = serde_json::from_slice(&plaintext).map_err(|_| invalid())?;
    if connection["id"] != ticket.pc_id || connection["app"] != "puente-portapapeles" {
        return Err(invalid());
    }
    Ok(json!({"ok":true,"kind":"pair_connection","connection":connection}))
}
