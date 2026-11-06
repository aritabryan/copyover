package dev.puente.android

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.UUID

/** Called only on Dispatchers.IO. Native code owns networking and HTTP authentication. */
class TransferRepository(private val context: Context, private val operationId: String? = null) {
    private val resolver get() = context.contentResolver

    fun ping(connection: Connection): TransferResult = call(connection, "ping")

    fun verifyIdentity(connection: Connection): TransferResult = call(connection, "verify_identity") {
        put("pc_id", connection.pcId)
    }

    class PairTicket(val server: String, val payload: JSONObject) {
        val code: String get() = payload.getString("code")
    }

    fun beginPair(pc: DiscoveredPc, phoneName: String): PairTicket {
        val request = JSONObject().put("op", "pair_start").put("server", pc.address)
            .put("pc_id", pc.id).put("name", phoneName)
        return PairTicket(pc.address, native(request).getJSONObject("ticket"))
    }

    fun pollPair(ticket: PairTicket): Connection? {
        val request = JSONObject().put("op", "pair_poll").put("server", ticket.server).put("ticket", ticket.payload)
        val response = native(request)
        if (response.getString("kind") == "pair_pending") return null
        return PairingCode.parse(response.getJSONObject("connection").toString())
    }

    fun checkOffer(connection: Connection): JSONObject? = native(connection.nativeRequest("offer_check")).optJSONObject("offer")

    fun downloadOffer(connection: Connection, id: String, operationId: String? = null): TransferResult {
        if (operationId != null) return TransferRepository(context, operationId).downloadOffer(connection, id)
        val offer = checkOffer(connection) ?: throw IOException(context.getString(R.string.offer_gone))
        require(offer.getString("id") == id) { context.getString(R.string.offer_changed) }
        val size = offer.getLong("size")
        require(FileSpacePolicy.fits(size, context.cacheDir.usableSpace)) { context.getString(R.string.no_space_for_file) }
        val directory = operationDir("received")
        return try { call(connection, "offer_download") {
            put("id", id); put("output_dir", directory.absolutePath)
            if (offer.has("sha256")) { put("size", size); put("sha256", offer.getString("sha256")) }
        } }
        catch (error: Exception) { directory.deleteRecursively(); throw error }
    }

    fun confirmOffer(connection: Connection, id: String) { call(connection, "offer_confirm") { put("id", id) } }

    fun send(connection: Connection, outgoing: Outgoing): TransferResult {
        if (outgoing.uris.isEmpty()) {
            val text = ContentPolicy.validateText(outgoing.text)
            require(text.isNotEmpty()) { context.getString(R.string.clipboard_empty_use_send_file) }
            return call(connection, "send_text") { put("text", text) }
        }
        val directory = operationDir("outgoing")
        return try {
            val files = stageFiles(directory, outgoing.uris)
            // Ordinary clipboard photos retain image-paste behavior on Windows.
            val photo = files.takeIf { it.length() == 1 }?.getJSONObject(0)
            val clipboardImage = photo != null && photo.getString("mime").startsWith("image/") &&
                File(photo.getString("path")).length() <= 32L * 1024 * 1024
            call(connection, if (clipboardImage) "send_files" else "send_stream_files") { put("files", files) }.also {
                if (!outgoing.sensitive) kotlinx.coroutines.runBlocking {
                    for (index in 0 until files.length()) {
                        val item = files.getJSONObject(index)
                        if (item.getString("mime").startsWith("image/")) runCatching { HistoryStore.get(context).recordImage(File(item.getString("path")), item.getString("mime")) }
                    }
                }
            }
        } finally {
            directory.deleteRecursively()
        }
    }

    fun receive(connection: Connection): TransferResult {
        val directory = operationDir("received")
        return try {
            call(connection, "receive") { put("output_dir", directory.absolutePath) }
                .also { if (it.file == null) directory.deleteRecursively() }
        } catch (error: Exception) {
            directory.deleteRecursively()
            throw error
        }
    }

    fun receiveAutomatic(connection: Connection, cursor: String): SyncResult {
        val directory = operationDir("received")
        return try {
            val response = native(connection.nativeRequest("sync_receive")
                .put("since", cursor).put("output_dir", directory.absolutePath))
            val result = parseResult(response)
            if (result.file == null) directory.deleteRecursively()
            SyncResult(response.getString("cursor"), result)
        } catch (error: Exception) {
            directory.deleteRecursively()
            throw error
        }
    }

    fun sendPreparedImage(connection: Connection, file: File, mime: String): TransferResult {
        require(mime.startsWith("image/") && file.length() in 1..(32L * 1024 * 1024)) { context.getString(R.string.image_invalid) }
        val files = JSONArray().put(JSONObject().put("path", file.absolutePath).put("name", file.name).put("mime", mime))
        return call(connection, "send_files") { put("files", files) }
    }

    fun save(file: ReceivedFile, destination: Uri) {
        val output = resolver.openOutputStream(destination) ?: throw IOException(context.getString(R.string.destination_open_failed))
        output.use { target -> file.file.inputStream().use { it.copyTo(target) } }
    }

    private fun stageFiles(directory: File, uris: List<Uri>): JSONArray {
        require(uris.size <= MAX_FILES) { context.resources.getQuantityString(R.plurals.too_many_files, MAX_FILES, MAX_FILES) }
        val files = JSONArray()
        uris.forEachIndexed { index, uri ->
            require(uri.scheme == "content") { context.getString(R.string.file_read_failed) }
            val name = displayName(uri)
            val file = File(directory, index.toString())
            declaredSize(uri)?.let { requireFileSpace(it) }
            val source = resolver.openInputStream(uri) ?: throw IOException(context.getString(R.string.file_read_failed_named, name))
            source.use { input ->
                operationId?.let { TransferService.stageStream(it, input) }
                file.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        operationId?.let { check(!TransferService.isCancelled(it)) { context.getString(R.string.transfer_cancelled) } }
                        val count = input.read(buffer)
                        if (count < 0) break
                        requireFileSpace(count.toLong())
                        output.write(buffer, 0, count)
                    }
                }
            }
            operationId?.let(TransferService::releaseStageStream)
            files.put(JSONObject().put("path", file.absolutePath).put("name", name)
                .put("mime", resolver.getType(uri) ?: "application/octet-stream"))
        }
        return files
    }

    private fun displayName(uri: Uri): String = resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
        ?.use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null }
        ?.let(ContentPolicy::safeName) ?: "file.bin"

    private fun operationDir(category: String): File {
        val root = File(context.cacheDir, category).apply { mkdirs() }
        val yesterday = System.currentTimeMillis() - 24 * 60 * 60 * 1000
        if (category != "received") root.listFiles()?.filter { it.lastModified() < yesterday }?.forEach { it.deleteRecursively() }
        return File(root, UUID.randomUUID().toString()).apply { check(mkdir()) { context.getString(R.string.no_space_for_transfer) } }
    }

    private fun declaredSize(uri: Uri): Long? = resolver.query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)
        ?.use { cursor -> if (cursor.moveToFirst() && !cursor.isNull(0)) cursor.getLong(0).takeIf { it >= 0 } else null }

    private fun requireFileSpace(bytes: Long) {
        if (!FileSpacePolicy.fits(bytes, context.cacheDir.usableSpace)) throw LocalizedException(R.string.no_space_for_transfer)
    }

    private fun call(connection: Connection, op: String, body: JSONObject.() -> Unit = {}): TransferResult {
        val request = connection.nativeRequest(op).apply(body)
        val response = native(request)
        return parseResult(response)
    }

    private fun parseResult(response: JSONObject): TransferResult {
        val message = NativeMessages.translate(context, response.getString("message"))
        return when (response.getString("kind")) {
            "address" -> TransferResult(message, address = response.getString("server"))
            "text" -> TransferResult(message, text = response.getString("text"))
            "file" -> TransferResult(message, file = receivedFile(response))
            else -> TransferResult(message)
        }
    }

    private fun native(request: JSONObject): JSONObject {
        val response = JSONObject(operationId?.let { NativeBridge.executeTracked(it, request.toString()) } ?: NativeBridge.execute(request.toString()))
        if (!response.getBoolean("ok")) throw NativeError(response.optString("code"), NativeMessages.translate(context, response.getString("message")))
        return response
    }

    private fun receivedFile(response: JSONObject): ReceivedFile {
        val source = File(response.getString("path"))
        val name = ContentPolicy.safeName(response.getString("name"))
        val target = File(source.parentFile, name)
        if (source != target && !source.renameTo(target)) throw IOException(context.getString(R.string.received_prepare_failed))
        return ReceivedFile(target, name, response.getString("mime"))
    }

    private companion object { const val MAX_FILES = 100 }
}
