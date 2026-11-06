use crate::{checked, response, Failure};
use serde_json::Value;
use std::path::Path;

fn valid_cursor(value: &str) -> bool {
    let Some((session, sequence)) = value.split_once(':') else {
        return false;
    };
    session.len() == 32
        && session
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
        && !sequence.is_empty()
        && sequence.len() <= 10
        && sequence.bytes().all(|c| c.is_ascii_digit())
        && sequence.parse::<u32>().is_ok()
}

pub(crate) fn receive(
    agent: &ureq::Agent,
    server: &str,
    key: &str,
    since: &str,
    directory: &Path,
) -> Result<Value, Failure> {
    if !since.is_empty() && !valid_cursor(since) {
        return Err(Failure::new(
            "protocol",
            "Invalid sync cursor.",
        ));
    }
    let response = checked(
        agent
            .get(&format!("{server}/sincronizar?desde={since}"))
            .set("X-Key", key)
            .call(),
    )?;
    let cursor = response.header("X-Puente-Version").unwrap_or("").to_owned();
    if !valid_cursor(&cursor) {
        return Err(Failure::new(
            "protocol",
            "Update CopyOver on the PC to receive automatically.",
        ));
    }
    let mut result = response::receive_limited(response, directory, 32 * 1024 * 1024)?;
    result["cursor"] = Value::String(cursor);
    Ok(result)
}
