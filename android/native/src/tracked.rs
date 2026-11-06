use serde_json::json;
use std::{
    cell::RefCell,
    collections::HashMap,
    io::{self, Read},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex, OnceLock,
    },
};
#[derive(Default)]
pub(crate) struct Progress {
    cancelled: AtomicBool,
    bytes: AtomicU64,
    total: AtomicU64,
    finished: AtomicBool,
}
static OPERATIONS: OnceLock<Mutex<HashMap<String, Arc<Progress>>>> = OnceLock::new();
thread_local! { static CURRENT: RefCell<Option<Arc<Progress>>> = const { RefCell::new(None) }; }
fn operations() -> &'static Mutex<HashMap<String, Arc<Progress>>> {
    OPERATIONS.get_or_init(Mutex::default)
}
pub fn execute_tracked_json(id: &str, request: &str) -> String {
    let progress = {
        let mut entries = operations().lock().unwrap();
        entries.retain(|key, v| key == id || !v.finished.load(Ordering::Relaxed));
        entries.entry(id.to_owned()).or_default().clone()
    };
    progress.finished.store(false, Ordering::Relaxed);
    CURRENT.with(|slot| *slot.borrow_mut() = Some(progress.clone()));
    let result = if check().is_ok() {
        crate::execute_json(request)
    } else {
        String::new()
    };
    CURRENT.with(|slot| *slot.borrow_mut() = None);
    progress.finished.store(true, Ordering::Relaxed);
    if progress.cancelled.load(Ordering::Relaxed) {
        json!({"ok":false,"code":"cancelled","message":"Transfer canceled. Anything that already arrived stays."}).to_string()
    } else {
        result
    }
}
pub fn cancel_transfer(id: &str) -> bool {
    let mut entries = operations().lock().unwrap();
    let existed = entries.contains_key(id);
    entries
        .entry(id.to_owned())
        .or_default()
        .cancelled
        .store(true, Ordering::Relaxed);
    existed
}
pub fn transfer_status_json(id: &str) -> String {
    let entries = operations().lock().unwrap();
    match entries.get(id) { Some(p) => json!({"bytes":p.bytes.load(Ordering::Relaxed),"total":p.total.load(Ordering::Relaxed),"finished":p.finished.load(Ordering::Relaxed),"cancelled":p.cancelled.load(Ordering::Relaxed)}).to_string(), None => "{}".into() }
}
pub(crate) fn active() -> bool {
    CURRENT.with(|s| s.borrow().is_some())
}
pub(crate) fn total(bytes: u64) {
    CURRENT.with(|s| {
        if let Some(p) = s.borrow().as_ref() {
            p.total.store(bytes, Ordering::Relaxed)
        }
    });
}
pub(crate) fn check() -> io::Result<()> {
    CURRENT.with(|s| {
        if s.borrow()
            .as_ref()
            .is_some_and(|p| p.cancelled.load(Ordering::Relaxed))
        {
            Err(io::Error::other("cancelled"))
        } else {
            Ok(())
        }
    })
}
pub(crate) struct Reader<R>(pub R);
impl<R: Read> Read for Reader<R> {
    fn read(&mut self, b: &mut [u8]) -> io::Result<usize> {
        check()?;
        let n = self.0.read(b)?;
        check()?;
        CURRENT.with(|s| {
            if let Some(p) = s.borrow().as_ref() {
                p.bytes.fetch_add(n as u64, Ordering::Relaxed);
            }
        });
        Ok(n)
    }
}
pub(crate) fn position(bytes: u64) {
    CURRENT.with(|s| {
        if let Some(p) = s.borrow().as_ref() {
            p.bytes.store(bytes, Ordering::Relaxed);
        }
    });
}
