package dev.puente.android

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

@Composable
fun EquipmentPanel(model: SettingsViewModel, refresh: () -> Unit) {
    var rename by remember { mutableStateOf<Connection?>(null) }
    var forget by remember { mutableStateOf<Connection?>(null) }
    var name by remember { mutableStateOf("") }
    Text(stringResource(R.string.devices_title), style = MaterialTheme.typography.titleLarge)
    model.connectionError?.let { reason -> ConnectionProblem(reason) }
    model.savedPcs.forEach { pc ->
        val id = PcCatalog.identity(pc)
        var menu by remember(id) { mutableStateOf(false) }
        Card(onClick = { model.choosePc(id); refresh() }, enabled = !model.busy,
            modifier = Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)) {
            Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                PuenteIcon(R.drawable.ic_pc)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(pc.name, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (id == model.activePcId) Text(stringResource(R.string.device_selected), style = MaterialTheme.typography.labelSmall)
                    Text(stringResource(if (pc.encrypted) R.string.pc_encrypted else R.string.pc_not_encrypted),
                        style = MaterialTheme.typography.labelSmall,
                        color = if (pc.encrypted) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error)
                }
                if (id == model.activePcId) PuenteIcon(R.drawable.ic_check, stringResource(R.string.pc_selected))
                Box {
                    IconButton(onClick = { menu = true }) { PuenteIcon(R.drawable.ic_more, stringResource(R.string.pc_options, pc.name)) }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        if (id != model.activePcId) DropdownMenuItem(text = { Text(stringResource(R.string.device_use)) }, enabled = !model.busy,
                            onClick = { menu = false; model.choosePc(id); refresh() })
                        DropdownMenuItem(text = { Text(stringResource(R.string.device_rename)) }, onClick = { menu = false; name = pc.name; rename = pc })
                        DropdownMenuItem(text = { Text(stringResource(R.string.device_unlink)) }, enabled = !model.busy, onClick = { menu = false; forget = pc })
                    }
                }
            }
        }
    }
    ActionButton(stringResource(R.string.link_another_pc), R.drawable.ic_plus, model::search, enabled = !model.busy)
    if (model.code.isNotEmpty()) Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(stringResource(R.string.pairing_code_title), style = MaterialTheme.typography.titleMedium)
            Text(model.code.chunked(3).joinToString(" "), style = MaterialTheme.typography.displaySmall)
            Text(stringResource(R.string.pairing_code_hint), style = MaterialTheme.typography.bodyMedium)
            if (model.needsConfirmation) ActionButton(stringResource(R.string.confirm_code), R.drawable.ic_check, model::confirmCode,
                enabled = !model.busy, primary = true)
        }
    }
    if (model.pcs.isNotEmpty()) Text(stringResource(R.string.nearby_pcs), style = MaterialTheme.typography.titleMedium)
    model.pcs.forEach { pc ->
        ActionRow(pc.name, R.drawable.ic_pc, { model.pair(pc) }, enabled = !model.busy && !model.needsConfirmation)
    }
    if (!model.busy && model.pcs.isEmpty() && model.code.isEmpty() && !model.connected) {
        Text(model.status, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    if (rename != null) AlertDialog(onDismissRequest = { rename = null }, title = { Text(stringResource(R.string.rename_title)) },
        text = { OutlinedTextField(value = name, onValueChange = { name = it.take(80) }, label = { Text(stringResource(R.string.rename_label)) }, singleLine = true) },
        confirmButton = { TextButton(onClick = { rename?.let { model.renamePc(PcCatalog.identity(it), name) }; rename = null }, enabled = name.isNotBlank()) { Text(stringResource(R.string.rename_save)) } },
        dismissButton = { TextButton(onClick = { rename = null }) { Text(stringResource(R.string.cancel)) } })
    if (forget != null) AlertDialog(onDismissRequest = { forget = null }, title = { Text(stringResource(R.string.unlink_title, forget?.name.orEmpty())) },
        text = { Text(stringResource(R.string.unlink_message)) },
        confirmButton = { TextButton(onClick = { forget?.let { model.forgetPc(PcCatalog.identity(it)); refresh() }; forget = null }) { Text(stringResource(R.string.device_unlink)) } },
        dismissButton = { TextButton(onClick = { forget = null }) { Text(stringResource(R.string.cancel)) } })
}

@Composable internal fun ConnectionProblem(reason: String) {
    var show by remember(reason) { mutableStateOf(false) }
    OutlinedButton(onClick = { show = true }) {
        PuenteIcon(R.drawable.ic_warning, stringResource(R.string.connection_problem))
        Spacer(Modifier.width(8.dp)); Text(stringResource(R.string.check_link))
    }
    if (show) AlertDialog(onDismissRequest = { show = false }, title = { Text(stringResource(R.string.link_title)) },
        text = { Text(reason) }, confirmButton = { TextButton(onClick = { show = false }) { Text(stringResource(R.string.close)) } })
}
