package dev.puente.android

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable
fun FilesPanel(model: SettingsViewModel, openAction: (String) -> Unit) {
    val context = LocalContext.current
    val monitor = remember { AndroidOfferMonitor.get(context) }
    val offer by monitor.pending.collectAsStateWithLifecycle()
    val error by monitor.error.collectAsStateWithLifecycle()
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(18.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        item { Text(stringResource(R.string.tab_files), style = MaterialTheme.typography.titleLarge) }
        item { ActionButton(stringResource(R.string.action_send_file), R.drawable.ic_attach, { openAction(QuickNotification.ATTACH) },
            enabled = model.paired && !model.busy, primary = true) }
        error?.let { message -> item { Text(message, color = MaterialTheme.colorScheme.error) } }
        offer?.let { value -> item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(stringResource(R.string.from_pc_named, value.pc), style = MaterialTheme.typography.labelMedium)
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        PuenteIcon(R.drawable.ic_file); Text(value.name, Modifier.weight(1f))
                    }
                    Text("${context.fileSize(value.size)} · ${stringResource(fileKind(value.mime))}", style = MaterialTheme.typography.bodySmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        ActionButton(stringResource(R.string.receive), R.drawable.ic_down, {
                            val connection = ConnectionStore(context).load()
                            if (connection.pcId == value.pcId) TransferService.start(context, connection, offerId = value.id)
                        }, primary = true)
                        TextButton(onClick = { monitor.dismiss(value) }) { Text(stringResource(R.string.dismiss)) }
                    }
                }
            }
        } } ?: run { if (!model.paired) item { Text(stringResource(R.string.link_pc_in_devices), color = MaterialTheme.colorScheme.onSurfaceVariant) } }
        item { TransferPanel() }
    }
}
