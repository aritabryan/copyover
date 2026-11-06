package dev.puente.android

import android.content.ClipData
import android.content.Intent
import android.net.Uri
import android.os.PersistableBundle
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class IncomingContentTest {
    @Test fun sensitiveClipboardFlagIsPreserved() {
        val clip = ClipData.newPlainText("secret", "sensitive text")
        clip.description.extras = PersistableBundle().apply {
            putBoolean("android.content.extra.IS_SENSITIVE", true)
        }
        assertTrue(IncomingContent.clipboard(clip).sensitive)
    }

    @Test fun sensitiveSharedClipFlagIsPreserved() {
        val clip = ClipData.newPlainText("secret", "sensitive text")
        clip.description.extras = PersistableBundle().apply {
            putBoolean("android.content.extra.IS_SENSITIVE", true)
        }
        val intent = Intent(Intent.ACTION_SEND).putExtra(Intent.EXTRA_TEXT, "sensitive text")
        intent.clipData = clip
        assertTrue(IncomingContent.shared(intent).sensitive)
    }

    @Test fun galleryClipDataWithoutExtraStreamIsAccepted() {
        val uri = Uri.parse("content://gallery/screenshots/123")
        val intent = Intent(Intent.ACTION_SEND).setType("image/png")
        intent.clipData = ClipData.newRawUri("captura", uri)
        assertEquals(listOf(uri), IncomingContent.shared(intent).uris)
    }

    @Test fun repeatedUriAcrossStreamAndClipDataIsSentOnce() {
        val uri = Uri.parse("content://gallery/screenshots/123")
        val intent = Intent(Intent.ACTION_SEND).setType("image/png").putExtra(Intent.EXTRA_STREAM, uri)
        intent.clipData = ClipData.newRawUri("captura", uri)
        assertEquals(listOf(uri), IncomingContent.shared(intent).uris)
    }
}
