package dev.puente.android

import android.content.Context
import androidx.annotation.StringRes
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject

enum class SyncDirection(@StringRes val label: Int) { FROM_PC(R.string.direction_from_pc), TO_PC(R.string.direction_to_pc) }
enum class SyncPhase(@StringRes val label: Int) {
    DISABLED(R.string.phase_disabled), SEARCHING(R.string.phase_searching), READY(R.string.phase_ready), WAITING(R.string.phase_waiting), SYNCING(R.string.phase_syncing),
    TRANSFERRING(R.string.phase_transferring), ERROR(R.string.phase_error)
}
data class DirectionState(val phase: SyncPhase = SyncPhase.DISABLED, val error: String = "",
                          val deliveredAt: Long = 0, val kind: String = "", val verifiedAt: Long = 0) {
    fun progress(next: SyncPhase, reason: String = "") = copy(phase = next, error = reason)
    fun delivered(now: Long, contentKind: String) = copy(phase = SyncPhase.WAITING, error = "", deliveredAt = now, kind = contentKind)
}

/** Only metadata is persisted; clipboard content never enters diagnostics. */
class SyncStatusStore private constructor(context: Context) {
    private val preferences = context.getSharedPreferences("sync_status", Context.MODE_PRIVATE)
    private val mutable = MutableStateFlow<Map<String, DirectionState>>(emptyMap())
    val states = mutable.asStateFlow()
    private fun key(pc: String, direction: SyncDirection) = "$pc:${direction.name}"

    @Synchronized fun state(pc: String, direction: SyncDirection): DirectionState {
        val id = key(pc, direction)
        mutable.value[id]?.let { return it }
        return runCatching {
            val json = JSONObject(preferences.getString(id, "{}").orEmpty())
            DirectionState(deliveredAt = json.optLong("at"), kind = json.optString("kind"))
        }.getOrDefault(DirectionState())
    }
    @Synchronized fun update(pc: String, direction: SyncDirection, phase: SyncPhase, error: String = "") {
        mutable.value = mutable.value + (key(pc, direction) to state(pc, direction).progress(phase, error))
    }
    @Synchronized fun delivered(pc: String, direction: SyncDirection, kind: String) {
        val value = state(pc, direction).delivered(System.currentTimeMillis(), kind)
        preferences.edit().putString(key(pc, direction), JSONObject().put("at", value.deliveredAt).put("kind", kind).toString()).apply()
        mutable.value = mutable.value + (key(pc, direction) to value)
    }
    @Synchronized fun verified(pc: String, direction: SyncDirection) {
        mutable.value = mutable.value + (key(pc, direction) to state(pc, direction).copy(verifiedAt = System.currentTimeMillis()))
    }
    @Synchronized fun forget(pc: String) {
        val editor = preferences.edit()
        SyncDirection.entries.forEach { editor.remove(key(pc, it)) }
        editor.apply()
        mutable.value = mutable.value.filterKeys { !it.startsWith("$pc:") }
    }
    companion object {
        @Volatile private var instance: SyncStatusStore? = null
        fun get(context: Context): SyncStatusStore = instance ?: synchronized(this) {
            instance ?: SyncStatusStore(context.applicationContext).also { instance = it }
        }
    }
}
