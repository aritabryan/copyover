package dev.puente.android

/** Serializes invalidation with publication, without holding a lock during network requests. */
class OfferGeneration {
    private var generation = 0L
    @Synchronized fun capture(): Long = generation
    @Synchronized fun invalidate(clear: () -> Unit) { generation++; clear() }
    @Synchronized fun publish(expected: Long, action: () -> Unit): Boolean {
        if (generation != expected) return false
        action()
        return true
    }
}
