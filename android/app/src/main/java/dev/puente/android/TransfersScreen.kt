package dev.puente.android

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import android.content.Intent
import android.content.ClipData
import kotlinx.coroutines.launch
import kotlinx.coroutines.Dispatchers

@Composable
fun TransferPanel() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var selected by remember { mutableStateOf<ReceivedFile?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    val save = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val file = selected
        val uri = result.data?.data
        if (result.resultCode == android.app.Activity.RESULT_OK && file != null && uri != null) scope.launch(Dispatchers.IO) {
            runCatching { TransferRepository(context).save(file, uri) }.onFailure { message = context.describe(it, R.string.destination_open_failed) }
        }
    }
    fun open(file: ReceivedFile, share: Boolean) {
        runCatching {
            val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file.file)
            val intent = if (share) Intent(Intent.ACTION_SEND).setType(file.mime).putExtra(Intent.EXTRA_STREAM, uri).apply { clipData = ClipData.newRawUri(file.name, uri) }
                else Intent(Intent.ACTION_VIEW).setDataAndType(uri, file.mime)
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            context.startActivity(if (share) Intent.createChooser(intent, context.getString(R.string.share_file)) else intent)
        }.onFailure { message = context.getString(R.string.no_open_app) }
    }
    Column {
        message?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        TransfersScreen(onSave = { file ->
            selected = file
            runCatching { save.launch(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(file.mime).putExtra(Intent.EXTRA_TITLE, file.name)) }
                .onFailure { message = context.getString(R.string.destination_picker_failed) }
        }, onShare = { open(it, true) }, onOpen = { open(it, false) })
    }
}

@Composable
fun TransfersScreen(onSave: (ReceivedFile) -> Unit, onShare: (ReceivedFile) -> Unit, onOpen: (ReceivedFile) -> Unit) {
    val context = LocalContext.current
    LaunchedEffect(Unit) { TransferRecords.initialize(context) }
    val state by TransferRecords.state.collectAsStateWithLifecycle()
    val received by TransferRecords.received.collectAsStateWithLifecycle()
    val files = received.filter { it.file.isFile }
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        if (state.busy) TransferProgress(state) { TransferService.cancel(context) }
        else if (state.failed) Text(state.message, color = MaterialTheme.colorScheme.error)
        if (files.isEmpty() && !state.busy) EmptyContent(R.drawable.ic_folder, stringResource(R.string.files_none_received))
        files.forEach { file ->
            Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        PuenteIcon(if (file.mime.startsWith("image/")) R.drawable.ic_image else R.drawable.ic_file)
                        Text(file.name, Modifier.weight(1f), maxLines = 2, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                    }
                    Text("${context.fileSize(file.file.length())} · ${stringResource(fileKind(file.mime))}", style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                        IconButton(onClick = { onSave(file) }) { PuenteIcon(R.drawable.ic_down, stringResource(R.string.save_as)) }
                        IconButton(onClick = { onShare(file) }) { PuenteIcon(R.drawable.ic_share, stringResource(R.string.share)) }
                        TextButton(onClick = { onOpen(file) }) { Text(stringResource(R.string.open)); Spacer(Modifier.width(6.dp)); PuenteIcon(R.drawable.ic_right) }
                    }
                }
            }
        }
    }
}

@Composable
fun TransferProgress(state: TransferState, onCancel: () -> Unit) {
    val context = LocalContext.current
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(stringResource(R.string.phase_transferring), style = MaterialTheme.typography.titleSmall)
                if (state.total > 0) Text(stringResource(R.string.percent, (state.bytes * 100 / state.total).coerceIn(0, 100).toInt()))
            }
            if (state.total > 0) LinearProgressIndicator(progress = { (state.bytes.toFloat() / state.total).coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
            else LinearProgressIndicator(Modifier.fillMaxWidth())
            Row(Modifier.fillMaxWidth(), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(if (state.total > 0) stringResource(R.string.bytes_of_total, context.fileSize(state.bytes), context.fileSize(state.total)) else context.fileSize(state.bytes), style = MaterialTheme.typography.bodySmall)
                    Text(stringResource(R.string.speed_per_second, context.fileSize(state.bytesPerSecond)), style = MaterialTheme.typography.labelSmall)
                }
                IconButton(onClick = onCancel) { PuenteIcon(R.drawable.ic_close, stringResource(R.string.cancel_transfer)) }
            }
        }
    }
}
