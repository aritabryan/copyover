package dev.puente.android

import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files

class TransferSafetyTest {
    @Test fun lateProgressCannotReviveFinishedTransferOrOverwriteAnotherOperation() {
        val completed = TransferState(finished = true, message = "Recibido")
        assertEquals(completed, TransferProgressPolicy.apply(completed, "old", 1, 10, 1))
        val next = TransferState(busy = true, operationId = "next")
        assertEquals(next, TransferProgressPolicy.apply(next, "old", 1, 10, 1))
        assertEquals(5L, TransferProgressPolicy.apply(next, "next", 5, 10, 1).bytes)
    }
    @Test fun processRestartRemovesPartialDownloadAndPreservesReceivedAndClipboardImages() {
        val directory = Files.createTempDirectory("puente-cache-test").toFile()
        try {
            val partial = java.io.File(directory, "incomplete/contenido").apply { parentFile!!.mkdirs(); writeText("partial") }
            val received = java.io.File(directory, "complete/file.txt").apply { parentFile!!.mkdirs(); writeText("keep") }
            val clipboard = java.io.File(directory, "clipboard/image.png").apply { parentFile!!.mkdirs(); writeText("keep") }
            ReceivedCachePolicy.clean(directory, setOf(received, clipboard))
            assertFalse(partial.exists())
            assertTrue(received.exists())
            assertTrue(clipboard.exists())
        } finally { directory.deleteRecursively() }
    }
}
