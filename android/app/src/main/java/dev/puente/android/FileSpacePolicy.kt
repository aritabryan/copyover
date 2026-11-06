package dev.puente.android

/** File transfers are limited by disk space. Clipboard payload limits live separately. */
object FileSpacePolicy {
    const val RESERVE_BYTES = 8L * 1024 * 1024
    fun fits(required: Long, available: Long): Boolean =
        required >= 0 && available >= RESERVE_BYTES && required <= available - RESERVE_BYTES
}
