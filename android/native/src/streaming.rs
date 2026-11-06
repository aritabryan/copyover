//! Disk-backed Android file protocol. Buffers never grow with file size.
use crate::{checked, multipart::Upload, tracked, Failure};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
    time::{Duration, Instant},
};
const BLOCK: usize = 64 * 1024;
fn protocol() -> Failure {
    Failure::new("protocol", "Invalid transfer response.")
}
fn network() -> Failure {
    Failure::new(
        "network",
        "The connection to the PC was interrupted. Try again.",
    )
}
fn checked_stream(result: Result<ureq::Response, ureq::Error>) -> Result<ureq::Response, Failure> {
    match result {
        Err(ureq::Error::Status(409 | 503, _)) => {
            Err(Failure::new("busy", "The PC is preparing the file."))
        }
        other => checked(other),
    }
}
fn hash_file(path: &Path) -> Result<(u64, String), Failure> {
    let mut file = File::open(path)?;
    let expected = file.metadata()?.len();
    let mut hash = Sha256::new();
    let mut buffer = [0; BLOCK];
    let mut length = 0u64;
    loop {
        tracked::check()?;
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        length = length.checked_add(count as u64).ok_or_else(protocol)?;
        hash.update(&buffer[..count]);
    }
    if length != expected {
        return Err(Failure::new(
            "changed",
            "The file changed while it was being sent.",
        ));
    }
    Ok((length, format!("{:x}", hash.finalize())))
}
fn json_response(response: ureq::Response) -> Result<Value, Failure> {
    let mut bytes = Vec::new();
    let mut reader = response.into_reader();
    let mut buffer = [0; 4096];
    loop {
        tracked::check()?;
        let count = reader.read(&mut buffer).map_err(|_| network())?;
        if count == 0 {
            break;
        }
        bytes.extend_from_slice(&buffer[..count]);
        if bytes.len() > 1024 * 1024 {
            return Err(protocol());
        }
    }
    serde_json::from_slice(&bytes).map_err(|_| protocol())
}
struct Reconnect {
    deadline: Option<Instant>,
}
impl Reconnect {
    fn new() -> Self {
        Self { deadline: None }
    }
    fn reset(&mut self) {
        self.deadline = None;
    }
    fn retry(&mut self, error: Failure) -> Result<(), Failure> {
        match error.code {
            "busy" => {
                self.reset();
                self.wait()
            }
            "network" => self.wait(),
            _ => Err(error),
        }
    }
    fn wait(&mut self) -> Result<(), Failure> {
        let deadline = *self
            .deadline
            .get_or_insert_with(|| Instant::now() + Duration::from_secs(90));
        for _ in 0..5 {
            tracked::check()?;
            if Instant::now() >= deadline {
                return Err(network());
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        Ok(())
    }
}
fn post(agent: &ureq::Agent, url: &str, key: &str, body: &Value) -> Result<Value, Failure> {
    let result = agent
        .post(url)
        .set("X-Key", key)
        .set("Content-Type", "application/json")
        .send_string(&body.to_string());
    json_response(checked_stream(result)?)
}
fn retry_json(mut operation: impl FnMut() -> Result<Value, Failure>) -> Result<Value, Failure> {
    let mut reconnect = Reconnect::new();
    loop {
        tracked::check()?;
        match operation() {
            Ok(value) => return Ok(value),
            Err(error) => reconnect.retry(error)?,
        }
    }
}
/// PCs not yet updated use these names in their JSON: incoming data is read with the current ones, and what
/// this app sends carries both.
const OLD_WIRE_NAMES: [(&str, &str); 6] = [
    ("archivos", "files"),
    ("nombre", "name"),
    ("oferta", "offer"),
    ("reanudar", "resume"),
    ("recibido", "received"),
    ("tamano", "size"),
];
fn from_wire(value: Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.into_iter().map(from_wire).collect()),
        Value::Object(fields) => {
            let mut result = serde_json::Map::new();
            for (key, item) in fields.iter() {
                let renamed = OLD_WIRE_NAMES.iter().find(|(old, _)| old == key);
                match renamed {
                    Some((_, new)) if fields.contains_key(*new) => {}
                    Some((_, new)) => {
                        result.insert((*new).to_string(), from_wire(item.clone()));
                    }
                    None => {
                        result.insert(key.clone(), from_wire(item.clone()));
                    }
                }
            }
            Value::Object(result)
        }
        other => other,
    }
}
pub(crate) fn offer_check(agent: &ureq::Agent, server: &str, key: &str) -> Result<Value, Failure> {
    let value = retry_json(|| {
        let result = agent
            .get(&format!("{server}/android/ofertas"))
            .set("X-Key", key)
            .call();
        match result {
            Err(ureq::Error::Status(404 | 405, _)) => Ok(json!({"offer":null})),
            Err(ureq::Error::Status(503, _)) => {
                Err(Failure::new("busy", "The PC is preparing the file."))
            }
            // Unlike upload offsets, an offer's 409 means its source is unavailable.
            other => json_response(checked(other)?),
        }
    })?;
    let value = from_wire(value);
    let offer = value.get("offer").ok_or_else(protocol)?;
    Ok(json!({"ok":true,"offer":offer}))
}
pub(crate) fn send(
    agent: &ureq::Agent,
    server: &str,
    key: &str,
    files: Vec<Upload>,
) -> Result<Value, Failure> {
    if files.is_empty() {
        return Err(Failure::new("empty", "There are no files to send."));
    }
    let base = format!("{server}/android/transferencias");
    let capability = retry_json(|| match agent.get(&base).set("X-Key", key).call() {
        Err(ureq::Error::Status(404 | 405, _)) => Err(Failure::new(
            "update_required",
            "Update CopyOver on the PC to send large files.",
        )),
        result => json_response(checked_stream(result)?),
    })?;
    if capability["version"] != 1 {
        return Err(Failure::new(
            "update_required",
            "Update CopyOver on the PC to send large files.",
        ));
    }
    let mut metadata = Vec::new();
    let mut sizes = Vec::new();
    let mut total = 0u64;
    for file in &files {
        let (size, hash) = hash_file(&file.path)?;
        total = total.checked_add(size).ok_or_else(protocol)?;
        sizes.push(size);
        metadata.push(json!({"name":file.name,"size":size,"sha256":hash,"nombre":file.name,"tamano":size}));
    }
    tracked::total(total);
    tracked::position(0);
    let created = from_wire(retry_json(|| {
        post(agent, &base, key, &json!({"files":metadata,"archivos":metadata}))
    })?);
    let id = created["id"].as_str().ok_or_else(protocol)?;
    crate::validate_offer_id(id)?;
    if created["resume"] != true {
        return Err(protocol());
    }
    let session = format!("{base}/{id}");
    let result = send_session(agent, &session, key, &files, &sizes);
    if tracked::check().is_err() {
        let _ = agent
            .post(&format!("{session}/cancelar"))
            .set("X-Key", key)
            .call();
    }
    result
}
fn offsets(
    agent: &ureq::Agent,
    session: &str,
    key: &str,
    sizes: &[u64],
) -> Result<Vec<(u64, bool)>, Failure> {
    let state = from_wire(post(agent, &format!("{session}/estado"), key, &json!({}))?);
    let entries = state["files"].as_array().ok_or_else(protocol)?;
    if entries.len() != sizes.len() {
        return Err(protocol());
    }
    entries
        .iter()
        .zip(sizes)
        .map(|(entry, size)| {
            let offset = entry["offset"].as_u64().ok_or_else(protocol)?;
            let received = entry["received"].as_bool().ok_or_else(protocol)?;
            if offset > *size || (received && offset != *size) {
                return Err(protocol());
            }
            Ok((offset, received))
        })
        .collect()
}
fn send_session(
    agent: &ureq::Agent,
    session: &str,
    key: &str,
    files: &[Upload],
    sizes: &[u64],
) -> Result<Value, Failure> {
    let mut reconnect = Reconnect::new();
    let mut confirmed = None;
    loop {
        tracked::check()?;
        let state = match offsets(agent, session, key, sizes) {
            Ok(state) => state,
            Err(e) => {
                reconnect.retry(e)?;
                continue;
            }
        };
        let position = state.iter().map(|(offset, _)| offset).sum();
        tracked::position(position);
        if state.iter().all(|(_, received)| *received) {
            return Ok(json!({"ok":true,"kind":"message","message":"Files sent"}));
        }
        if confirmed.is_some_and(|previous| position <= previous) {
            reconnect.wait()?;
        } else {
            reconnect.reset();
        }
        confirmed = Some(position);
        for (index, ((offset, received), upload)) in state.iter().zip(files).enumerate() {
            if *received {
                continue;
            }
            if let Err(error) =
                upload_file(agent, session, key, index, upload, sizes[index], *offset)
            {
                reconnect.retry(error)?;
                break;
            }
        }
    }
}
fn upload_file(
    agent: &ureq::Agent,
    session: &str,
    key: &str,
    index: usize,
    upload: &Upload,
    size: u64,
    offset: u64,
) -> Result<(), Failure> {
    tracked::check()?;
    let mut file = File::open(&upload.path)?;
    if file.metadata()?.len() != size {
        return Err(Failure::new(
            "changed",
            "The file changed while it was being sent.",
        ));
    }
    file.seek(SeekFrom::Start(offset))?;
    let result = agent
        .put(&format!("{session}/{index}"))
        .set("X-Key", key)
        .set("X-Puente-Offset", &offset.to_string())
        .set("Content-Type", "application/octet-stream")
        .set("Content-Length", &(size - offset).to_string())
        .send(tracked::Reader(file.take(size - offset)));
    checked_stream(result)?;
    Ok(())
}
pub(crate) fn download(
    agent: &ureq::Agent,
    server: &str,
    key: &str,
    id: &str,
    output: &Path,
    size: u64,
    sha256: &str,
) -> Result<Value, Failure> {
    if sha256.len() != 64
        || !sha256
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    {
        return Err(protocol());
    }
    fs::create_dir_all(output)?;
    let partial = output.join("contenido.partial");
    let destination = output.join("contenido");
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&partial)?;
    tracked::total(size);
    tracked::position(0);
    let result = download_loop(
        agent,
        &format!("{server}/android/ofertas/{id}/archivo"),
        key,
        &mut file,
        size,
    );
    drop(file);
    let result=result.and_then(|(name,mime)| {
        let(actual,hash)=hash_file(&partial)?;
        if actual!=size || hash!=sha256 { return Err(Failure::new("hash","The received file doesn't match the original.")); }
        if destination.exists() { return Err(protocol()); }
        tracked::check()?;
        fs::rename(&partial,&destination)?;
        Ok(json!({"ok":true,"kind":"file","path":destination,"name":name,"mime":mime,"message":"File received"}))
    });
    if result.is_err() {
        let _ = fs::remove_file(partial);
    }
    result
}
fn download_loop(
    agent: &ureq::Agent,
    url: &str,
    key: &str,
    file: &mut File,
    size: u64,
) -> Result<(String, String), Failure> {
    let mut offset = 0u64;
    let mut reconnect = Reconnect::new();
    let mut metadata = None;
    loop {
        tracked::check()?;
        let mut request = agent.get(url).set("X-Key", key);
        if offset > 0 {
            request = request.set("Range", &format!("bytes={offset}-"));
        }
        let response = match checked_stream(request.call()) {
            Err(e) => {
                reconnect.retry(e)?;
                continue;
            }
            Ok(response) => response,
        };
        validate_download(&response, offset, size)?;
        let mime = response
            .header("Content-Type")
            .unwrap_or("application/octet-stream")
            .split(';')
            .next()
            .unwrap_or("application/octet-stream")
            .trim()
            .to_owned();
        if metadata.is_none() {
            metadata = Some((
                crate::response::download_name(
                    response.header("Content-Disposition").unwrap_or(""),
                    &mime,
                ),
                mime,
            ));
        }
        copy_response(
            response.into_reader(),
            file,
            size,
            &mut offset,
            &mut reconnect,
        )?;
        if offset == size {
            file.flush()?;
            return metadata.ok_or_else(protocol);
        }
        reconnect.wait()?;
    }
}
fn copy_response(
    mut reader: impl Read,
    file: &mut File,
    size: u64,
    offset: &mut u64,
    reconnect: &mut Reconnect,
) -> Result<(), Failure> {
    let mut buffer = [0; BLOCK];
    while *offset < size {
        tracked::check()?;
        let limit = (size - *offset).min(BLOCK as u64) as usize;
        let count = match reader.read(&mut buffer[..limit]) {
            Ok(0) | Err(_) => break,
            Ok(count) => count,
        };
        file.write_all(&buffer[..count])?;
        *offset += count as u64;
        reconnect.reset();
        tracked::position(*offset);
    }
    Ok(())
}
fn validate_download(response: &ureq::Response, offset: u64, size: u64) -> Result<(), Failure> {
    let length = response
        .header("Content-Length")
        .and_then(|h| h.parse::<u64>().ok())
        .ok_or_else(protocol)?;
    if length != size - offset {
        return Err(protocol());
    }
    if offset == 0 {
        if response.status() != 200 {
            return Err(protocol());
        }
    } else if response.status() != 206
        || response.header("Content-Range")
            != Some(format!("bytes {offset}-{}/{size}", size - 1).as_str())
    {
        return Err(protocol());
    }
    Ok(())
}
