package dev.puente.android

import android.content.Context
import android.text.format.Formatter
import androidx.annotation.StringRes

/** An error from plain rules that have no Context; the text is resolved when it's shown. */
class LocalizedException(@StringRes val text: Int, private vararg val args: Any) : IllegalArgumentException() {
    fun resolve(context: Context): String = context.getString(text, *args)
}

fun Context.describe(error: Throwable, @StringRes fallback: Int): String = when (error) {
    is LocalizedException -> error.resolve(this)
    else -> error.message?.takeIf { it.isNotBlank() }?.let { NativeMessages.translate(this, it) } ?: getString(fallback)
}

fun Context.fileSize(bytes: Long): String = Formatter.formatShortFileSize(this, bytes.coerceAtLeast(0))

@StringRes fun fileKind(mime: String): Int {
    val type = mime.lowercase()
    return when {
        type == "application/pdf" -> R.string.kind_pdf
        type.startsWith("image/") -> R.string.kind_image
        type.startsWith("video/") -> R.string.kind_video
        type.startsWith("audio/") -> R.string.kind_audio
        type.startsWith("text/") -> R.string.kind_text
        type in setOf("application/zip", "application/x-7z-compressed", "application/x-rar-compressed",
            "application/vnd.rar", "application/gzip", "application/x-tar") -> R.string.kind_archive
        else -> R.string.kind_file
    }
}

@StringRes fun contentKind(kind: String): Int = when (kind) {
    "text", "texto" -> R.string.content_text
    "image", "imagen" -> R.string.content_image
    else -> R.string.content_file
}

/** The Rust core answers in English; this converts its messages to the system language. */
object NativeMessages {
    private val known = mapOf(
        "The PC rejected this request. Check the link in Devices." to R.string.receive_link_rejected,
        "Couldn't verify your PC. Restart CopyOver on the PC or link it again." to R.string.native_verify_failed,
        "PC verified" to R.string.native_pc_verified,
        "Couldn't read or save the content. Check the available space." to R.string.native_io_failed,
        "Invalid request." to R.string.native_invalid_request,
        "Link your PC again." to R.string.relink_pc,
        "Invalid PC address. Link it again." to R.string.native_bad_address,
        "Invalid offer." to R.string.offer_invalid,
        "Receipt confirmed" to R.string.native_receipt_confirmed,
        "Invalid update response." to R.string.update_invalid,
        "Invalid update." to R.string.update_invalid,
        "That address isn't a PC running CopyOver." to R.string.native_not_copyover,
        "PC connected, key valid" to R.string.native_pc_connected,
        "Unexpected response from the PC." to R.string.native_unexpected,
        "The clipboard is empty." to R.string.native_clipboard_empty,
        "The key changed. Link your PC again." to R.string.native_key_changed,
        "The upload exceeds the 500 MiB limit." to R.string.upload_too_large,
        "The PC couldn't finish the operation. Check its log." to R.string.native_pc_failed,
        "Couldn't find your PC. Check the Wi-Fi and that CopyOver is open." to R.string.pc_not_found,
        "Redirects aren't allowed. Check the PC's address." to R.string.native_redirect,
        "There are no files to send." to R.string.native_no_files,
        "Couldn't start sending." to R.string.native_send_start_failed,
        "Invalid file type." to R.string.native_bad_type,
        "Couldn't link this PC. Search for it again." to R.string.link_failed_search_again,
        "The PC declined the connection." to R.string.native_pair_rejected,
        "The PC answered with a different certificate and nothing was sent. If you reinstalled CopyOver on the PC, link it again." to R.string.native_certificate_mismatch,
        "The request expired. Try again." to R.string.status_request_expired,
        "The content exceeds the 500 MiB limit." to R.string.upload_too_large,
        "The received text is too large." to R.string.native_text_too_large,
        "The received text isn't UTF-8." to R.string.native_text_unreadable,
        "The PC's clipboard is empty." to R.string.native_pc_clipboard_empty,
        "The update file is too large." to R.string.native_update_too_large,
        "Text received" to R.string.native_text_received,
        "The received file is too large." to R.string.native_file_too_large,
        "File received" to R.string.file_received,
        "Invalid sync cursor." to R.string.native_sync_failed,
        "Update CopyOver on the PC to receive automatically." to R.string.native_update_pc,
        "Transfer canceled. Anything that already arrived stays." to R.string.native_cancelled,
        "Invalid transfer response." to R.string.file_protocol_invalid,
        "The connection to the PC was interrupted. Try again." to R.string.file_connection_lost,
        "The file changed while it was being sent." to R.string.file_changed_during_send,
        "Update CopyOver on the PC to send large files." to R.string.file_update_pc,
        "The received file doesn't match the original." to R.string.file_hash_mismatch,
        "Files sent" to R.string.files_sent,
    )

    fun translate(context: Context, message: String): String = known[message]?.let(context::getString) ?: message
}
