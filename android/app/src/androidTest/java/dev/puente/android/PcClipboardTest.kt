package dev.puente.android

import android.content.ClipData
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class PcClipboardTest {
    @Test fun onlyPcContentReceivesLoopPreventionMarker() {
        val local = ClipData.newPlainText("Chrome", "copiado en Android")
        assertFalse(local.description.extras?.getBoolean(PcClipboard.ORIGIN, false) == true)
        val remote = PcClipboard.mark(ClipData.newPlainText("Desde la PC", "Windows"))
        assertTrue(remote.description.extras!!.getBoolean(PcClipboard.ORIGIN, false))
        assertEquals("Windows", remote.getItemAt(0).text.toString())
    }
}
