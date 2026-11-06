package dev.puente.android

enum class SyncIndicator { DISABLED, BUSY, ACTIVE, ATTENTION }

fun syncIndicator(state: DirectionState): SyncIndicator = when (state.phase) {
    SyncPhase.DISABLED -> SyncIndicator.DISABLED
    SyncPhase.SEARCHING, SyncPhase.SYNCING, SyncPhase.TRANSFERRING -> SyncIndicator.BUSY
    SyncPhase.WAITING -> SyncIndicator.ACTIVE
    SyncPhase.READY, SyncPhase.ERROR -> SyncIndicator.ATTENTION
}

fun latestDelivery(states: List<DirectionState>): DirectionState? =
    states.filter { it.deliveredAt > 0 }.maxByOrNull { it.deliveredAt }

enum class RelativeUnit { NOW, MINUTES, HOURS, DAYS }
data class RelativeTime(val unit: RelativeUnit, val amount: Long = 0)

/** Pure and locale-free; the label is resolved from resources when shown. */
fun relativeDeliveryTime(deliveredAt: Long, now: Long): RelativeTime {
    val minutes = ((now - deliveredAt).coerceAtLeast(0) / 60_000)
    return when {
        minutes == 0L -> RelativeTime(RelativeUnit.NOW)
        minutes < 60 -> RelativeTime(RelativeUnit.MINUTES, minutes)
        minutes < 1440 -> RelativeTime(RelativeUnit.HOURS, minutes / 60)
        else -> RelativeTime(RelativeUnit.DAYS, minutes / 1440)
    }
}
