use jni::objects::{JObject, JString};
use jni::sys::jstring;
use jni::JNIEnv;

#[no_mangle]
pub extern "system" fn Java_dev_puente_android_NativeBridge_execute(
    mut env: JNIEnv,
    _object: JObject,
    request: JString,
) -> jstring {
    let input: String = match env.get_string(&request) {
        Ok(value) => value.into(),
        Err(_) => return std::ptr::null_mut(), // JNI exception is left pending.
    };
    let result = crate::execute_json(&input);
    match env.new_string(result) {
        Ok(value) => value.into_raw(),
        Err(_) => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub extern "system" fn Java_dev_puente_android_NativeBridge_executeTracked(
    mut env: JNIEnv,
    _object: JObject,
    id: JString,
    request: JString,
) -> jstring {
    let id: String = match env.get_string(&id) {
        Ok(v) => v.into(),
        Err(_) => return std::ptr::null_mut(),
    };
    let request: String = match env.get_string(&request) {
        Ok(v) => v.into(),
        Err(_) => return std::ptr::null_mut(),
    };
    env.new_string(crate::execute_tracked_json(&id, &request))
        .map(|s| s.into_raw())
        .unwrap_or(std::ptr::null_mut())
}
#[no_mangle]
pub extern "system" fn Java_dev_puente_android_NativeBridge_progress(
    mut env: JNIEnv,
    _object: JObject,
    id: JString,
) -> jstring {
    let id: String = match env.get_string(&id) {
        Ok(v) => v.into(),
        Err(_) => return std::ptr::null_mut(),
    };
    env.new_string(crate::transfer_status_json(&id))
        .map(|s| s.into_raw())
        .unwrap_or(std::ptr::null_mut())
}
#[no_mangle]
pub extern "system" fn Java_dev_puente_android_NativeBridge_cancel(
    mut env: JNIEnv,
    _object: JObject,
    id: JString,
) {
    if let Ok(id) = env.get_string(&id) {
        crate::cancel_transfer(&String::from(id));
    }
}
