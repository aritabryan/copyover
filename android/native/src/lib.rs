//! HTTP protocol shared with the existing Python server; no Android APIs here.
mod identity;
mod multipart;
mod pairing;
mod response;
mod streaming;
mod sync;
mod tls;
mod tracked;
pub use tracked::{cancel_transfer, execute_tracked_json, transfer_status_json};
#[cfg(target_os = "android")]
mod android_jni;
use serde::Deserialize;
use serde_json::{json, Value};
use std::time::Duration;
pub const MAX_BYTES: u64 = 500 * 1024 * 1024;
#[derive(Debug)]
pub(crate) struct Failure {
    code: &'static str,
    message: String,
}
impl Failure {
    pub(crate) fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}
impl From<std::io::Error> for Failure {
    fn from(_: std::io::Error) -> Self {
        Self::new(
            "io",
            "Couldn't read or save the content. Check the available space.",
        )
    }
}
#[derive(Deserialize)]
struct Command {
    server: String,
    #[serde(default)]
    key: String,
    /// CA of the PC in PEM, stored when it was linked. With it the PC is reached over HTTPS.
    #[serde(default)]
    ca: String,
    #[serde(flatten)]
    operation: Operation,
}
#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum Operation {
    PairStart {
        name: String,
        pc_id: String,
    },
    PairPoll {
        ticket: pairing::Ticket,
    },
    Ping,
    VerifyIdentity {
        pc_id: String,
    },
    SendText {
        text: String,
    },
    SendFiles {
        files: Vec<multipart::Upload>,
    },
    SendStreamFiles {
        files: Vec<multipart::Upload>,
    },
    Receive {
        output_dir: std::path::PathBuf,
    },
    SyncReceive {
        since: String,
        output_dir: std::path::PathBuf,
    },
    OfferCheck,
    OfferDownload {
        id: String,
        size: Option<u64>,
        sha256: Option<String>,
        output_dir: std::path::PathBuf,
    },
    OfferConfirm {
        id: String,
    },
    UpdateCheck,
    UpdateDownload {
        sha256: String,
        output_dir: std::path::PathBuf,
    },
}
pub fn execute_json(request: &str) -> String {
    let result = serde_json::from_str::<Command>(request)
        .map_err(|_| Failure::new("invalid_request", "Invalid request."))
        .and_then(execute);
    match result {
        Ok(value) => value.to_string(),
        Err(error) => {
            json!({"ok": false, "code": error.code, "message": error.message}).to_string()
        }
    }
}
fn validate(command: &Command) -> Result<String, Failure> {
    let url = url::Url::parse(command.server.trim())
        .map_err(|_| Failure::new("config", "Link your PC again."))?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(Failure::new(
            "config",
            "Invalid PC address. Link it again.",
        ));
    }
    // HTTPS only with the pinned CA, and a pinned PC only over HTTPS: never downgrade.
    if (url.scheme() == "https") == command.ca.is_empty() {
        return Err(Failure::new("config", "Link your PC again."));
    }
    let pairing = matches!(
        &command.operation,
        Operation::PairStart { .. } | Operation::PairPoll { .. }
    );
    if !pairing && (command.key.is_empty() || !command.key.bytes().all(|c| (33..=126).contains(&c)))
    {
        return Err(Failure::new("config", "Link your PC again."));
    }
    Ok(url.as_str().trim_end_matches('/').to_owned())
}
fn execute(command: Command) -> Result<Value, Failure> {
    let server = validate(&command)?;
    let pairing = matches!(
        &command.operation,
        Operation::PairStart { .. } | Operation::PairPoll { .. }
    );
    let interruptible = tracked::active()
        || matches!(
            &command.operation,
            Operation::OfferCheck
                | Operation::SendStreamFiles { .. }
                | Operation::OfferDownload { size: Some(_), .. }
        );
    let io_timeout = Duration::from_secs(if interruptible { 3 } else { 180 });
    // Disallow redirects: X-Key must never be forwarded to a different host.
    let builder = ureq::AgentBuilder::new()
        .redirects(0)
        .timeout_connect(Duration::from_secs(3))
        .timeout_read(io_timeout)
        .timeout_write(io_timeout);
    let builder = if command.ca.is_empty() {
        builder
    } else {
        builder.tls_connector(std::sync::Arc::new(tls::Connector::new(&command.ca)?))
    };
    let agent = if tracked::active() {
        builder
    } else {
        builder.timeout(Duration::from_secs(if pairing {
            12
        } else if matches!(&command.operation, Operation::SyncReceive { .. }) {
            40
        } else {
            180
        }))
    }
    .build();
    match command.operation {
        Operation::OfferCheck => streaming::offer_check(&agent, &server, &command.key),
        Operation::OfferDownload {
            id,
            output_dir,
            size,
            sha256,
        } => {
            if let (Some(size), Some(hash)) = (size, sha256.as_deref()) {
                validate_offer_id(&id)?;
                return streaming::download(
                    &agent,
                    &server,
                    &command.key,
                    &id,
                    &output_dir,
                    size,
                    hash,
                );
            }
            if size.is_some() || sha256.is_some() {
                return Err(Failure::new("protocol", "Invalid offer."));
            }
            validate_offer_id(&id)?;
            let response = checked(
                agent
                    .get(&format!("{server}/android/ofertas/{id}/archivo"))
                    .set("X-Key", &command.key)
                    .call(),
            )?;
            response::receive(response, &output_dir)
        }
        Operation::OfferConfirm { id } => {
            validate_offer_id(&id)?;
            checked(
                agent
                    .post(&format!("{server}/android/ofertas/{id}/confirmar"))
                    .set("X-Key", &command.key)
                    .call(),
            )?;
            Ok(json!({"ok":true,"kind":"message","message":"Receipt confirmed"}))
        }
        Operation::SyncReceive { since, output_dir } => {
            sync::receive(&agent, &server, &command.key, &since, &output_dir)
        }
        Operation::UpdateCheck => {
            let response = checked(
                agent
                    .get(&format!("{server}/android/actualizacion"))
                    .set("X-Key", &command.key)
                    .call(),
            )?;
            let text = response::read_small(response, 8192)?;
            let value: Value = serde_json::from_str(&text)
                .map_err(|_| Failure::new("protocol", "Invalid update response."))?;
            Ok(json!({"ok":true,"update":value["update"],"message":value["message"]}))
        }
        Operation::UpdateDownload { sha256, output_dir } => {
            if sha256.len() != 64
                || !sha256
                    .bytes()
                    .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
            {
                return Err(Failure::new("protocol", "Invalid update."));
            }
            let response = checked(
                agent
                    .get(&format!("{server}/android/apk/{sha256}"))
                    .set("X-Key", &command.key)
                    .call(),
            )?;
            response::receive_limited(response, &output_dir, 200 * 1024 * 1024)
        }
        Operation::PairStart { name, pc_id } => pairing::start(&agent, &server, &name, &pc_id),
        Operation::PairPoll { ticket } => pairing::poll(&agent, &server, ticket),
        Operation::VerifyIdentity { pc_id } => {
            identity::verify(&agent, &server, &command.key, &pc_id)
        }
        Operation::Ping => {
            let response = checked(agent.get(&format!("{server}/ping")).call())?;
            let body = response::read_small(response, 1024)?;
            if body != "Puente activo" {
                return Err(Failure::new(
                    "protocol",
                    "That address isn't a PC running CopyOver.",
                ));
            }
            // /ping is public; validate the key without reading or modifying the clipboard.
            // A harmless authenticated empty send yields 400 with a valid key.
            let probe = agent
                .post(&format!("{server}/send"))
                .set("X-Key", &command.key)
                .set("Content-Length", "0")
                .call();
            match probe {
                Err(ureq::Error::Status(400, _)) => {
                    Ok(json!({"ok":true,"kind":"message","message":"PC connected, key valid"}))
                }
                other => {
                    checked(other)?;
                    Err(Failure::new("protocol", "Unexpected response from the PC."))
                }
            }
        }
        Operation::SendText { text } => {
            if text.is_empty() {
                return Err(Failure::new("empty", "The clipboard is empty."));
            }
            response::check_size(text.len() as u64)?;
            let response = checked(
                agent
                    .post(&format!("{server}/send"))
                    .set("X-Key", &command.key)
                    .set("Content-Type", "text/plain; charset=utf-8")
                    .send_bytes(text.as_bytes()),
            )?;
            message(response)
        }
        Operation::SendStreamFiles { files } => {
            streaming::send(&agent, &server, &command.key, files)
        }
        Operation::SendFiles { files } => {
            let body = multipart::Multipart::new(files)?;
            tracked::total(body.length);
            let response = checked(
                agent
                    .post(&format!("{server}/send"))
                    .set("X-Key", &command.key)
                    .set(
                        "Content-Type",
                        &format!("multipart/form-data; boundary={}", body.boundary),
                    )
                    .set("Content-Length", &body.length.to_string())
                    .send(tracked::Reader(body)),
            )?;
            message(response)
        }
        Operation::Receive { output_dir } => {
            let response = checked(
                agent
                    .get(&format!("{server}/receive"))
                    .set("X-Key", &command.key)
                    .call(),
            )?;
            response::receive(response, &output_dir)
        }
    }
}
fn message(response: ureq::Response) -> Result<Value, Failure> {
    Ok(json!({"ok":true,"kind":"message","message":response::read_small(response, 64 * 1024)?}))
}
fn checked(result: Result<ureq::Response, ureq::Error>) -> Result<ureq::Response, Failure> {
    let response = match result {
        Ok(response) => response,
        Err(ureq::Error::Status(401, _)) => {
            return Err(Failure::new(
                "unauthorized",
                "The key changed. Link your PC again.",
            ))
        }
        Err(ureq::Error::Status(403, _)) => {
            return Err(Failure::new(
                "forbidden",
                "The PC rejected this request. Check the link in Devices.",
            ))
        }
        Err(ureq::Error::Status(413, _)) => {
            return Err(Failure::new(
                "too_large",
                "The upload exceeds the 500 MiB limit.",
            ))
        }
        Err(ureq::Error::Status(_, _)) => {
            return Err(Failure::new(
                "server",
                "The PC couldn't finish the operation. Check its log.",
            ))
        }
        Err(ureq::Error::Transport(transport)) if tls::certificate_rejected(&transport) => {
            return Err(Failure::new(
                "certificate",
                "The PC answered with a different certificate and nothing was sent. If you reinstalled CopyOver on the PC, link it again.",
            ))
        }
        Err(ureq::Error::Transport(_)) => {
            return Err(Failure::new(
                "network",
                "Couldn't find your PC. Check the Wi-Fi and that CopyOver is open.",
            ))
        }
    };
    if (300..400).contains(&response.status()) {
        return Err(Failure::new(
            "redirect",
            "Redirects aren't allowed. Check the PC's address.",
        ));
    }
    Ok(response)
}
fn validate_offer_id(id: &str) -> Result<(), Failure> {
    if id.is_empty()
        || id.len() > 128
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        return Err(Failure::new("protocol", "Invalid offer."));
    }
    Ok(())
}
