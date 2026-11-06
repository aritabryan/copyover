package dev.puente.android

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject
import org.json.JSONArray
import java.io.File

/** Process-wide state survives activities; private durable record detects process interruption. */
object TransferRecords {
    private val mutable = MutableStateFlow(TransferState())
    val state = mutable.asStateFlow()
    private val receivedMutable = MutableStateFlow<List<ReceivedFile>>(emptyList())
    val received = receivedMutable.asStateFlow()
    private var initialized = false
    @Synchronized fun initialize(context: Context) {
        if (initialized) return
        initialized = true
        val preferences = context.getSharedPreferences("transfers", Context.MODE_PRIVATE)
        runCatching {
            val array = JSONArray(preferences.getString("received", "[]"))
            receivedMutable.value = (0 until array.length()).mapNotNull { index ->
                val item = array.getJSONObject(index)
                File(item.getString("path")).takeIf { it.isFile }?.let { ReceivedFile(it, item.getString("name"), item.getString("mime")) }
            }
        }
        val raw = context.getSharedPreferences("transfers", Context.MODE_PRIVATE).getString("latest", null) ?: return
        runCatching {
            val item = JSONObject(raw)
            val file = item.optString("path").takeIf { it.isNotEmpty() }?.let(::File)?.takeIf { it.isFile }
            val result = file?.let { TransferResult(context.getString(R.string.file_received), file = ReceivedFile(it, item.getString("name"), item.getString("mime"))) }
            mutable.value = TransferState(finished = true, failed = item.optBoolean("busy"), message = if (item.optBoolean("busy")) context.getString(R.string.transfer_interrupted_retry) else item.getString("message"), result = result)
        }
    }
    @Synchronized fun update(context: Context, state: TransferState) {
        mutable.value = state
        state.result?.file?.let { file ->
            val all = listOf(file) + receivedMutable.value.filter { it.file != file.file }
            receivedMutable.value = all.take(20)
            all.drop(20).forEach { it.file.delete(); it.file.parentFile?.delete() }
            val array = JSONArray()
            receivedMutable.value.forEach { array.put(JSONObject().put("path", it.file.absolutePath).put("name", it.name).put("mime", it.mime)) }
            context.getSharedPreferences("transfers", Context.MODE_PRIVATE).edit().putString("received", array.toString()).apply()
        }
        val item = JSONObject().put("busy", state.busy).put("message", state.message)
        state.result?.file?.let { item.put("path", it.file.absolutePath).put("name", it.name).put("mime", it.mime) }
        context.getSharedPreferences("transfers", Context.MODE_PRIVATE).edit().putString("latest", item.toString()).apply()
    }
    @Synchronized fun progress(context: Context, id: String, bytes: Long, total: Long, speed: Long, message: String) {
        val next = TransferProgressPolicy.apply(mutable.value, id, bytes, total, speed, message)
        if (next != mutable.value) update(context, next)
    }
}
