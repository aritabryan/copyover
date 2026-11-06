package dev.puente.android

import android.net.Uri
import java.io.File

data class Outgoing(val text: String = "", val uris: List<Uri> = emptyList(), val sensitive: Boolean = false)
data class ReceivedFile(val file: File, val name: String, val mime: String)
data class TransferResult(val message: String, val text: String? = null, val file: ReceivedFile? = null,
    val address: String? = null)
data class SyncResult(val cursor: String, val content: TransferResult)
data class TransferState(
    val busy: Boolean = false,
    val finished: Boolean = false,
    val message: String = "",
    val result: TransferResult? = null,
    val textCopied: Boolean = false,
    val failed: Boolean = false,
    val operationId: String? = null,
    val bytes: Long = 0,
    val total: Long = 0,
    val bytesPerSecond: Long = 0,
)
