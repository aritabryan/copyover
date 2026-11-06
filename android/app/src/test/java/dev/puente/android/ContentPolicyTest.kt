package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class ContentPolicyTest {
    @Test fun restoredFocusGateDoesNotRelaunchPickerAfterProcessDeath() {
        val original = FocusGate()
        assertTrue(original.claim(true))
        val restored = FocusGate()
        restored.restoreClaimed(original.wasClaimed)
        assertFalse(restored.claim(true))
    }
    @Test fun filenamesCannotEscapeCacheOrInjectHeaders() {
        assertEquals("_.._foto_.png", ContentPolicy.safeName("/../foto\n.png"))
        assertEquals("file.bin", ContentPolicy.safeName("..."))
    }

    @Test fun unicodeFilenamesFitAndroidByteLimitAndKeepExtension() {
        val name = ContentPolicy.safeName("é".repeat(130) + ".pdf")
        assertTrue(name.toByteArray(Charsets.UTF_8).size <= 200)
        assertTrue(name.endsWith(".pdf"))
        val emoji = ContentPolicy.safeName("👋".repeat(100) + ".png")
        assertTrue(emoji.toByteArray(Charsets.UTF_8).size <= 200)
        assertTrue(emoji.endsWith(".png"))
        assertFalse(emoji.contains('\uFFFD'))
        assertFalse(emoji.dropLast(4).last().isHighSurrogate())
    }

    @Test fun clipboardTextLimitIsCheckedBeforeTransfer() {
        assertEquals("", ContentPolicy.validateText(""))
        assertEquals("Hola 👋", ContentPolicy.validateText("Hola 👋"))
        assertTrue(runCatching { ContentPolicy.validateText("a".repeat(4 * 1024 * 1024 + 1)) }.isFailure)
    }

    @Test fun clipboardIsOnlyConsumedOnceAfterWindowGainsFocus() {
        val gate = FocusGate()
        assertFalse(gate.claim(false))
        assertTrue(gate.claim(true))
        assertFalse(gate.claim(true))
        assertFalse(gate.claim(false))
    }
}
