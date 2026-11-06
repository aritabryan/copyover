use crate::{response::check_size, Failure};
use serde::Deserialize;
use std::collections::VecDeque;
use std::fs::File;
use std::io::{self, Cursor, Read};
use std::path::PathBuf;

#[derive(Deserialize)]
pub(crate) struct Upload {
    pub(crate) path: PathBuf,
    pub(crate) name: String,
    mime: String,
}

pub(crate) struct Multipart {
    pub boundary: String,
    pub length: u64,
    parts: VecDeque<Box<dyn Read>>,
}

impl Multipart {
    pub fn new(files: Vec<Upload>) -> Result<Self, Failure> {
        if files.is_empty() {
            return Err(Failure::new("empty", "There are no files to send."));
        }
        let mut random = [0_u8; 16];
        getrandom::getrandom(&mut random)
            .map_err(|_| Failure::new("random", "Couldn't start sending."))?;
        let boundary = format!("puente{:032x}", u128::from_le_bytes(random));
        let mut body = Self {
            boundary,
            length: 0,
            parts: VecDeque::new(),
        };
        for file in files {
            body.push_file(file)?;
        }
        body.push_bytes(format!("--{}--\r\n", body.boundary).into_bytes())?;
        Ok(body)
    }

    fn push_file(&mut self, upload: Upload) -> Result<(), Failure> {
        if upload.mime.is_empty() || !upload.mime.bytes().all(|b| (32..=126).contains(&b)) {
            return Err(Failure::new("mime", "Invalid file type."));
        }
        let name: String = upload
            .name
            .chars()
            .map(|c| {
                if c.is_control() || "\"\\/".contains(c) {
                    '_'
                } else {
                    c
                }
            })
            .collect();
        let file = File::open(&upload.path)?;
        let length = file.metadata()?.len();
        // Shortcuts sends clipboard text as a named text/plain part. Android's
        // explicit file shares must not trigger that legacy conversion.
        let mime = if upload.mime.eq_ignore_ascii_case("text/plain") {
            "application/octet-stream"
        } else {
            &upload.mime
        };
        self.push_bytes(format!("--{}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{}\"\r\nContent-Type: {}\r\n\r\n",
            self.boundary, if name.is_empty() { "file" } else { &name }, mime).into_bytes())?;
        self.length += length;
        check_size(self.length)?;
        // Bound the reader even if an external process changes the staged file.
        self.parts.push_back(Box::new(file.take(length)));
        self.push_bytes(b"\r\n".to_vec())
    }

    fn push_bytes(&mut self, bytes: Vec<u8>) -> Result<(), Failure> {
        self.length += bytes.len() as u64;
        check_size(self.length)?;
        self.parts.push_back(Box::new(Cursor::new(bytes)));
        Ok(())
    }
}

impl Read for Multipart {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if buffer.is_empty() {
            return Ok(0);
        }
        while let Some(part) = self.parts.front_mut() {
            let read = part.read(buffer)?;
            if read > 0 {
                return Ok(read);
            }
            self.parts.pop_front();
        }
        Ok(0)
    }
}
