package dev.puente.android

import android.content.ClipboardManager
import android.content.ClipData
import android.content.Intent
import android.net.Uri
import androidx.core.content.IntentCompat

object IncomingContent {
    fun clipboard(manager: ClipboardManager): Outgoing = clipboard(manager.primaryClip)

    fun clipboard(clip: ClipData?): Outgoing {
        clip ?: return Outgoing()
        val uris = mutableListOf<Uri>()
        val texts = mutableListOf<String>()
        for (index in 0 until clip.itemCount) {
            val item = clip.getItemAt(index)
            if (item.uri != null) uris.add(item.uri) else item.text?.toString()?.let(texts::add)
        }
        return Outgoing(texts.joinToString("\n"), uris, HistoryStore.isSensitive(clip.description))
    }

    fun shared(intent: Intent): Outgoing {
        require(intent.action in listOf(Intent.ACTION_SEND, Intent.ACTION_SEND_MULTIPLE)) { "Invalid shared content." }
        val uris = when (intent.action) {
            Intent.ACTION_SEND_MULTIPLE -> IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
            else -> listOfNotNull(IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java))
        }
        val text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString().orEmpty()
        val clipUris = intent.clipData?.let { clip ->
            (0 until clip.itemCount).mapNotNull { clip.getItemAt(it).uri }
        }.orEmpty()
        return Outgoing(text, (uris + clipUris).distinct(), HistoryStore.isSensitive(intent.clipData?.description))
    }
}
