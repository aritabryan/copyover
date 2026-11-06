use crate::{Failure, MAX_BYTES};
use percent_encoding::percent_decode_str;
use serde_json::{json, Value};
use std::fs::{self, OpenOptions};
use std::io::{self, Read};
use std::path::Path;
pub(crate) fn check_size(size: u64) -> Result<(), Failure> {
    if size > MAX_BYTES {
        return Err(Failure::new(
            "too_large",
            "The content exceeds the 500 MiB limit.",
        ));
    }
    Ok(())
}
pub(crate) fn read_small(response: ureq::Response, limit: u64) -> Result<String, Failure> {
    let mut bytes = Vec::new();
    crate::tracked::Reader(response.into_reader())
        .take(limit + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(Failure::new(
            "too_large",
            "The received text is too large.",
        ));
    }
    String::from_utf8(bytes).map_err(|_| Failure::new("encoding", "The received text isn't UTF-8."))
}
pub(crate) fn receive(response: ureq::Response, output_dir: &Path) -> Result<Value, Failure> {
    receive_limited(response, output_dir, MAX_BYTES)
}
pub(crate) fn receive_limited(
    response: ureq::Response,
    output_dir: &Path,
    limit: u64,
) -> Result<Value, Failure> {
    if response.status() == 204 {
        return Ok(
            json!({"ok":true,"kind":"empty","message":"The PC's clipboard is empty."}),
        );
    }
    if let Some(size) = response
        .header("Content-Length")
        .and_then(|s| s.parse::<u64>().ok())
    {
        check_size(size)?;
        crate::tracked::total(size);
        if size > limit {
            return Err(Failure::new(
                "too_large",
                "The update file is too large.",
            ));
        }
    }
    let mime = response
        .header("Content-Type")
        .unwrap_or("application/octet-stream")
        .split(';')
        .next()
        .unwrap_or("application/octet-stream")
        .trim()
        .to_owned();
    let disposition = response
        .header("Content-Disposition")
        .unwrap_or("")
        .to_owned();
    // A named .txt/.csv file remains a downloadable file, matching /receive's contract.
    if mime == "text/plain" && disposition.is_empty() {
        return Ok(
            json!({"ok":true,"kind":"text","text":read_small(response, 4 * 1024 * 1024)?,"message":"Text received"}),
        );
    }
    fs::create_dir_all(output_dir)?;
    let path = output_dir.join("contenido");
    // Each operation has its own private directory, supplied by Android.
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)?;
    let copied = io::copy(
        &mut crate::tracked::Reader(response.into_reader()).take(limit + 1),
        &mut file,
    );
    drop(file);
    let result = copied.map_err(Failure::from).and_then(|size| {
        if size > limit {
            Err(Failure::new(
                "too_large",
                "The received file is too large.",
            ))
        } else {
            Ok(())
        }
    });
    if let Err(error) = result {
        let _ = fs::remove_file(&path);
        return Err(error);
    }
    let name = download_name(&disposition, &mime);
    Ok(
        json!({"ok":true,"kind":"file","path":path,"name":name,"mime":mime,"message":"File received"}),
    )
}
pub(crate) fn download_name(disposition: &str, mime: &str) -> String {
    for parameter in disposition.split(';').skip(1).map(str::trim) {
        if let Some(value) = parameter.strip_prefix("filename*=UTF-8''") {
            return safe_name(&percent_decode_str(value).decode_utf8_lossy());
        }
        if let Some(value) = parameter.strip_prefix("filename=") {
            return safe_name(value.trim_matches('"'));
        }
    }
    match mime {
        "image/png" => "image.png",
        "image/jpeg" => "image.jpg",
        _ => "file.bin",
    }
    .to_owned()
}
fn safe_name(name: &str) -> String {
    let name: String = name
        .chars()
        .take(180)
        .map(|c| {
            if c.is_control() || "/\\:*?\"<>|".contains(c) {
                '_'
            } else {
                c
            }
        })
        .collect();
    let name = name.trim_matches([' ', '.']);
    if name.is_empty() {
        "file.bin".to_owned()
    } else {
        name.to_owned()
    }
}
