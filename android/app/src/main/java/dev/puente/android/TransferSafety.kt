package dev.puente.android

import java.io.File

object TransferProgressPolicy {
    fun apply(current: TransferState, id: String, bytes: Long, total: Long, speed: Long, message: String = current.message): TransferState {
        if (!current.busy || current.operationId != id) return current
        return current.copy(bytes = bytes, total = total, bytesPerSecond = speed, message = message)
    }
}

/** Called once at process start, before activities or services can create new receive operations. */
object ReceivedCachePolicy {
    fun clean(root: File, retained: Set<File>) {
        val prefix = root.canonicalPath + File.separator
        val keep = retained.map { it.canonicalPath }.toSet()
        root.listFiles()?.filter { it.isDirectory }?.forEach { directory ->
            val resolved = directory.canonicalPath
            if (resolved.startsWith(prefix) && keep.none { it.startsWith(resolved + File.separator) }) directory.deleteRecursively()
        }
    }
}
