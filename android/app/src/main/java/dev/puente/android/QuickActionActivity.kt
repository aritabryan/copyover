package dev.puente.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay

/**
 * Notification buttons, tiles, icon shortcuts and the widget go directly to this Activity; never a trampoline.
 * It stays a small panel over the current app, because Android only lets a focused window read the clipboard.
 */
open class QuickActionActivity : ComponentActivity() {
    private val model: TransferViewModel by viewModels()
    protected open val fromShare = false
    private var selectedTarget: Connection? = null
    private var shareRequest = ShareRequest()
    private var savedPcs by mutableStateOf(emptyList<Connection>())
    private var actionStarted by mutableStateOf(false)
    private val saveDocument = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == RESULT_OK) result.data?.data?.let(model::save)
    }
    private val attachDocuments = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isEmpty()) finish() else { model.send(Outgoing(uris = uris.distinct())); actionStarted = true }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        shareRequest = ShareRequest(savedInstanceState?.getBoolean("share_started") == true)
        model.focusGate.restoreClaimed(savedInstanceState?.getBoolean("action_started") == true)
        actionStarted = savedInstanceState?.getBoolean("panel_started") == true
        setContent {
            val state by model.state.collectAsStateWithLifecycle()
            var target by remember { mutableStateOf<Connection?>(null) }
            LaunchedEffect(state.result) { copyReceivedTextIfFocused() }
            if (!fromShare) PuenteTheme { QuickPanel(state) }
            else PuenteTheme {
                Scaffold { insets ->
                    Column(Modifier.fillMaxSize().padding(insets).padding(24.dp).verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(18.dp)) {
                        Text(stringResource(if (fromShare) R.string.send_to_pc else R.string.app_name), style = MaterialTheme.typography.headlineMedium)
                        if (fromShare && shareRequest.canChoose(state.busy) && savedPcs.isEmpty()) {
                            Text(stringResource(R.string.link_pc_first))
                            ActionButton(stringResource(R.string.link_a_pc), R.drawable.ic_plus, ::openDevices, primary = true)
                        } else if (fromShare && shareRequest.canChoose(state.busy)) {
                            Text(stringResource(R.string.choose_target_pc))
                            savedPcs.forEach { pc ->
                                ActionButton(pc.name, if (target?.pcId == pc.pcId) R.drawable.ic_check else R.drawable.ic_pc,
                                    { target = pc; selectedTarget = pc })
                            }
                            ActionButton(stringResource(R.string.send_to_pc), R.drawable.ic_up, ::sendShared, enabled = target != null, primary = true)
                        } else {
                            Text(state.message.ifEmpty { stringResource(if (state.busy) R.string.preparing else R.string.transfers_none) }, color = if (state.failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
                        }
                        if (state.busy) {
                            TransferProgress(state, model::cancel)
                        }
                        state.result?.file?.let { file ->
                            Text(file.name)
                            ActionButton(stringResource(R.string.save_as), R.drawable.ic_down, { saveAs(file) }, enabled = !state.busy, primary = true)
                            ActionButton(stringResource(R.string.share_file), R.drawable.ic_share, { share(file) }, enabled = !state.busy)
                        }
                        if (state.finished && state.failed) {
                            ActionButton(stringResource(R.string.send_image_or_file), R.drawable.ic_attach, { attachDocuments.launch(arrayOf("*/*")) })
                            ActionButton(stringResource(R.string.open_app), R.drawable.ic_pc, { startActivity(Intent(this@QuickActionActivity, MainActivity::class.java)); finish() })
                        }
                        ActionButton(stringResource(R.string.close), R.drawable.ic_close, { finish() }, enabled = !state.busy)
                    }
                }
            }
        }
    }

    @Composable
    private fun QuickPanel(state: TransferState) {
        val working = !actionStarted || state.busy
        LaunchedEffect(state, actionStarted) {
            if (QuickPanelPolicy.canAutoClose(actionStarted, state)) {
                delay(QuickPanelPolicy.CLOSE_DELAY_MS)
                finish()
            }
        }
        Box(Modifier.fillMaxSize().clickable(remember { MutableInteractionSource() }, null, enabled = !working) { finish() }
            .statusBarsPadding().padding(16.dp), contentAlignment = Alignment.TopCenter) {
            // While the file picker is on its way there is nothing to report yet.
            if (actionStarted || intent.action != QuickNotification.ATTACH) Surface(Modifier.widthIn(max = 480.dp).fillMaxWidth(),
                shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surfaceContainer, shadowElevation = 8.dp) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        when {
                            working -> CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.5.dp)
                            state.failed -> Icon(painterResource(R.drawable.ic_warning), null, Modifier.size(22.dp), MaterialTheme.colorScheme.error)
                            else -> PuenteIcon(R.drawable.ic_check)
                        }
                        Text(if (actionStarted) state.message.ifEmpty { stringResource(R.string.preparing) } else stringResource(R.string.preparing),
                            color = if (state.failed && !working) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface, maxLines = 4)
                    }
                    if (state.busy && state.total > 0) TransferProgress(state, model::cancel)
                    if (!working) state.result?.file?.let { file ->
                        Text(file.name)
                        ActionButton(stringResource(R.string.save_as), R.drawable.ic_down, { saveAs(file) }, Modifier.fillMaxWidth(), primary = true)
                        ActionButton(stringResource(R.string.share_file), R.drawable.ic_share, { share(file) }, Modifier.fillMaxWidth())
                    }
                    if (actionStarted && state.finished && state.failed) {
                        ActionButton(stringResource(R.string.send_image_or_file), R.drawable.ic_attach, { attachDocuments.launch(arrayOf("*/*")) }, Modifier.fillMaxWidth())
                        ActionButton(stringResource(R.string.open_app), R.drawable.ic_pc, { startActivity(Intent(this@QuickActionActivity, MainActivity::class.java)); finish() }, Modifier.fillMaxWidth())
                    }
                    if (!working && !QuickPanelPolicy.canAutoClose(actionStarted, state))
                        ActionButton(stringResource(R.string.close), R.drawable.ic_close, { finish() }, Modifier.fillMaxWidth())
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Reloaded on return so a PC paired from the share screen appears without sharing again.
        if (fromShare) savedPcs = runCatching { ConnectionStore(this).list() }.getOrDefault(emptyList())
    }

    private fun openDevices() {
        startActivity(Intent(this, MainActivity::class.java).putExtra(StartTab.EXTRA, "devices"))
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("share_started", shareRequest.started)
        outState.putBoolean("action_started", model.focusGate.wasClaimed)
        outState.putBoolean("panel_started", actionStarted)
        super.onSaveInstanceState(outState)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) copyReceivedTextIfFocused()
        if (fromShare || !model.focusGate.claim(hasFocus)) return
        when (intent.action) {
            QuickNotification.SEND -> readAndSendClipboard()
            QuickNotification.RECEIVE -> { model.receive(); actionStarted = true }
            QuickNotification.ATTACH -> {
                try { attachDocuments.launch(arrayOf("*/*")) }
                catch (_: Exception) { failAction(R.string.file_picker_failed) }
            }
            else -> failAction(R.string.unknown_action)
        }
    }

    private fun readAndSendClipboard() {
        try { model.send(IncomingContent.clipboard(getSystemService(ClipboardManager::class.java))); actionStarted = true }
        catch (_: Exception) { failAction(R.string.clipboard_read_failed) }
    }

    private fun failAction(message: Int) {
        model.fail(getString(message))
        actionStarted = true
    }

    private fun sendShared() {
        try {
            val target = selectedTarget ?: return
            val outgoing = IncomingContent.shared(intent)
            if (shareRequest.claim()) model.send(outgoing, target)
        }
        catch (_: Exception) { model.fail(getString(R.string.shared_content_failed)) }
    }

    private fun copyReceivedTextIfFocused() {
        if (fromShare && !shareRequest.started) return
        val state = model.state.value
        val text = state.result?.text ?: return
        if (!hasWindowFocus() || state.textCopied) return
        try {
            getSystemService(ClipboardManager::class.java).setPrimaryClip(PcClipboard.mark(ClipData.newPlainText(getString(R.string.clip_label_from_pc), text)))
            model.textCopied()
        } catch (_: Exception) { model.fail(getString(R.string.copy_text_failed)) }
    }

    private fun saveAs(file: ReceivedFile) {
        val request = Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType(file.mime).putExtra(Intent.EXTRA_TITLE, file.name)
        try { saveDocument.launch(request) }
        catch (_: Exception) { model.fail(getString(R.string.no_save_app)) }
    }

    private fun share(file: ReceivedFile) {
        val uri = FileProvider.getUriForFile(this, "$packageName.files", file.file)
        val request = Intent(Intent.ACTION_SEND).setType(file.mime)
            .putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            .apply { clipData = ClipData.newRawUri(file.name, uri) }
        try { startActivity(Intent.createChooser(request, getString(R.string.share_named, file.name))) }
        catch (_: Exception) { model.fail(getString(R.string.no_share_app)) }
    }
}

/** Explicit user confirmation prevents external apps from silently sending data to the PC. */
class ShareActivity : QuickActionActivity() {
    override val fromShare = true
}
