package dev.puente.android

import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.core.content.FileProvider
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

enum class HistoryNotice { TEXT_TOO_LONG, IMAGE_TOO_LARGE, PIN_LIMIT, DAMAGED }

/** All content lives in noBackupFilesDir. Calls suspend for serialized, bounded IO. */
class HistoryStore(private val directory: File, private val clock: () -> Long = System::currentTimeMillis) {
    private val mutex = Mutex()
    private var loaded = false
    private var exportDirectory: File? = null
    private val mutableEntries = MutableStateFlow<List<HistoryEntry>>(emptyList())
    val entries = mutableEntries.asStateFlow()
    private val mutableEnabled = MutableStateFlow(true)
    val enabled = mutableEnabled.asStateFlow()
    private val mutableMessage = MutableStateFlow<HistoryNotice?>(null)
    val message = mutableMessage.asStateFlow()
    private val index get() = File(directory, "index.json")

    private suspend fun <T> locked(block: suspend () -> T): T = withContext(Dispatchers.IO) {
        mutex.withLock {
            if (!loaded) load()
            block()
        }
    }

    suspend fun refresh() = locked { replace(HistoryPolicy.retain(entries.value, clock())) }
    suspend fun setEnabled(value: Boolean) = locked { mutableEnabled.value = value; persist(entries.value) }

    suspend fun recordText(text: String, sensitive: Boolean = false): Boolean = locked {
        if (!enabled.value || sensitive || text.isBlank()) return@locked false
        if (text.length > HistoryPolicy.MAX_TEXT) {
            mutableMessage.value = HistoryNotice.TEXT_TOO_LONG
            return@locked false
        }
        val fingerprint = digest(text.toByteArray(Charsets.UTF_8))
        if (isDuplicate(fingerprint, HistoryKind.TEXT)) return@locked false
        val item = HistoryEntry(UUID.randomUUID().toString(), clock(), HistoryKind.TEXT, text, fingerprint)
        replace(HistoryPolicy.retain(listOf(item) + entries.value, clock()))
        mutableMessage.value = null
        true
    }

    /** Source must be an app-owned stable file; it is streamed, never read wholly into memory. */
    suspend fun recordImage(source: File, mime: String, sensitive: Boolean = false): Boolean = locked {
        if (!enabled.value || sensitive || !mime.startsWith("image/") || !source.isFile) return@locked false
        val size = source.length()
        if (size <= 0 || size > HistoryPolicy.IMAGE_BUDGET) return@locked rejectImage()
        val retained = HistoryPolicy.retain(entries.value, clock())
        val room = HistoryPolicy.makeImageRoom(retained, size) ?: return@locked rejectImage()
        val id = UUID.randomUUID().toString()
        val target = File(directory, "$id.image")
        try {
            val fingerprint = copyImage(source, target, size)
            if (isDuplicate(fingerprint, HistoryKind.IMAGE)) { target.delete(); return@locked false }
            val item = HistoryEntry(id, clock(), HistoryKind.IMAGE, target.name, fingerprint, imageBytes = size, mime = mime)
            replace(HistoryPolicy.retain(listOf(item) + room, clock()))
            mutableMessage.value = null
            true
        } catch (error: Exception) {
            target.delete()
            throw error
        }
    }

    private fun copyImage(source: File, target: File, size: Long): String {
        val hash = MessageDigest.getInstance("SHA-256")
        var copied = 0L
        source.inputStream().buffered().use { input ->
            FileOutputStream(target).use { output ->
                val buffer = ByteArray(32 * 1024)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    copied += count
                    check(copied <= size) { "The image changed while being copied" }
                    hash.update(buffer, 0, count)
                    output.write(buffer, 0, count)
                }
                output.fd.sync()
            }
        }
        check(copied == size) { "The image changed while being copied" }
        return hash.digest().joinToString("") { "%02x".format(it) }
    }

    suspend fun pin(id: String, value: Boolean): Boolean = locked {
        val item = entries.value.find { it.id == id } ?: return@locked false
        if (value && !item.pinned && entries.value.count { it.pinned } >= HistoryPolicy.MAX_PINS) {
            mutableMessage.value = HistoryNotice.PIN_LIMIT
            return@locked false
        }
        replace(HistoryPolicy.retain(entries.value.map { if (it.id == id) it.copy(pinned = value) else it }, clock()))
        true
    }
    suspend fun delete(id: String) = locked { replace(entries.value.filterNot { it.id == id }) }
    suspend fun clearRecent() = locked { replace(entries.value.filter { it.pinned }) }

    /** Uses the same suppression marker as received PC content to prevent Shizuku echo. */
    internal suspend fun copyEntry(id: String, publish: suspend (HistoryEntry) -> Boolean): Boolean = locked {
        val item = entries.value.find { it.id == id } ?: return@locked false
        publish(item)
    }

    suspend fun copyToClipboard(context: Context, id: String): Boolean = copyEntry(id) { item ->
        val clip = if (item.kind == HistoryKind.TEXT) ClipData.newPlainText(context.getString(R.string.clip_label_history), item.content) else {
            val uri = withContext(Dispatchers.IO) {
                val source = File(directory, item.content)
                if (!source.isFile) return@withContext null
                val exportDir = File(context.cacheDir, "received").apply { mkdirs() }
                // Stable per-entry URI; history deletion also cleans this exported copy.
                val export = File(exportDir, exportedName(item))
                source.copyTo(export, overwrite = true)
                FileProvider.getUriForFile(context, "${context.packageName}.files", export)
            } ?: return@copyEntry false
            ClipData(context.getString(R.string.clip_label_history), arrayOf(item.mime), ClipData.Item(uri))
        }
        withContext(Dispatchers.Main) {
            context.getSystemService(ClipboardManager::class.java).setPrimaryClip(PcClipboard.mark(clip))
        }
        true
    }

    suspend fun thumbnail(id: String): Bitmap? = locked {
        val item = entries.value.find { it.id == id && it.kind == HistoryKind.IMAGE } ?: return@locked null
        val path = File(directory, item.content).absolutePath
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(path, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return@locked null
        var sample = 1
        while (bounds.outWidth / sample > 256 || bounds.outHeight / sample > 256) sample *= 2
        BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = sample })
    }

    private fun isDuplicate(hash: String, kind: HistoryKind): Boolean = HistoryPolicy.retain(entries.value, clock()).maxByOrNull { it.createdAt }
        ?.let { it.fingerprint == hash && it.kind == kind } == true
    private fun rejectImage(): Boolean {
        mutableMessage.value = HistoryNotice.IMAGE_TOO_LARGE
        return false
    }
    private fun replace(next: List<HistoryEntry>) {
        persist(next)
        val kept = next.filter { it.kind == HistoryKind.IMAGE }.map { it.content }.toSet()
        directory.listFiles()?.filter { it.name.endsWith(".image") && it.name !in kept }?.forEach { it.delete() }
        val exportNames = next.filter { it.kind == HistoryKind.IMAGE }.map(::exportedName).toSet()
        exportDirectory?.listFiles()?.filter { it.name.startsWith("history-") && it.name !in exportNames }?.forEach { it.delete() }
        mutableEntries.value = next
    }
    private fun persist(next: List<HistoryEntry>) {
        directory.mkdirs()
        val items = JSONArray()
        next.forEach { item -> items.put(JSONObject().apply {
            put("id", item.id); put("createdAt", item.createdAt); put("kind", item.kind.name)
            put("content", item.content); put("fingerprint", item.fingerprint); put("pinned", item.pinned)
            put("imageBytes", item.imageBytes); put("mime", item.mime)
        }) }
        val temporary = File(directory, "index.tmp")
        FileOutputStream(temporary).use { output ->
            output.write(JSONObject().put("enabled", enabled.value).put("items", items).toString().toByteArray(Charsets.UTF_8))
            output.fd.sync()
        }
        Files.move(temporary.toPath(), index.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }
    private fun load() {
        directory.mkdirs()
        val restored = try { readIndex() } catch (_: Exception) {
            mutableMessage.value = HistoryNotice.DAMAGED
            emptyList()
        }
        loaded = true
        replace(HistoryPolicy.retain(restored, clock()))
    }
    private fun readIndex(): List<HistoryEntry> {
        if (!index.isFile) return emptyList()
        // Bound allocation independently of JSON parsing, including corrupt metadata.
        check(index.length() <= 130L * 1024 * 1024) { "Historial demasiado grande" }
        val json = JSONObject(index.readText(Charsets.UTF_8))
        mutableEnabled.value = json.optBoolean("enabled", true)
        val items = json.getJSONArray("items")
        return (0 until minOf(items.length(), HistoryPolicy.MAX_RECENT + HistoryPolicy.MAX_PINS))
            .map { decodeEntry(items.getJSONObject(it)) }.filter(::validStoredEntry)
    }
    private fun decodeEntry(item: JSONObject) = HistoryEntry(
        item.getString("id"), item.getLong("createdAt"), HistoryKind.valueOf(item.getString("kind")),
        item.getString("content"), item.getString("fingerprint"), item.optBoolean("pinned"),
        item.optLong("imageBytes"), item.optString("mime", "text/plain"),
    )
    private fun validStoredEntry(entry: HistoryEntry): Boolean = entry.kind == HistoryKind.TEXT ||
        (File(entry.content).name == entry.content && File(directory, entry.content).isFile)
    private fun exportedName(entry: HistoryEntry): String {
        val extension = when (entry.mime.lowercase()) {
            "image/jpeg" -> "jpg"
            "image/png" -> "png"
            "image/gif" -> "gif"
            "image/webp" -> "webp"
            "image/bmp" -> "bmp"
            "image/heic" -> "heic"
            "image/heif" -> "heif"
            "image/avif" -> "avif"
            "image/svg+xml" -> "svg"
            else -> "image"
        }
        return "history-${entry.id}.$extension"
    }
    private fun digest(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    companion object {
        @Volatile private var instance: HistoryStore? = null
        fun get(context: Context): HistoryStore = instance ?: synchronized(this) {
            instance ?: HistoryStore(File(context.applicationContext.noBackupFilesDir, "clipboard-history")).also {
                it.exportDirectory = File(context.applicationContext.cacheDir, "received")
                instance = it
            }
        }
        fun isSensitive(description: ClipDescription?): Boolean =
            description?.extras?.getBoolean("android.content.extra.IS_SENSITIVE", false) == true
    }
}
