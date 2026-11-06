package dev.puente.android

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp

@Composable fun Dashboard(model: SettingsViewModel, updates: AppUpdateViewModel, shizuku: ShizukuSettingsModel,
                         tab: Int, onTab: (Int) -> Unit, openAction: (String) -> Unit,
                         toggleNotification: (Boolean) -> Unit, refresh: () -> Unit, install: () -> Unit,
                         fileNotificationsAllowed: Boolean = true, requestFileNotifications: () -> Unit = {}) {
    Scaffold(
        topBar = { DashboardHeader(model) },
        bottomBar = {
            NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                val labels = listOf(R.string.history_title, R.string.tab_files, R.string.tab_devices, R.string.tab_settings)
                val icons = listOf(R.drawable.ic_clipboard, R.drawable.ic_folder, R.drawable.ic_pc, R.drawable.ic_settings)
                labels.forEachIndexed { index, label ->
                    NavigationBarItem(selected = tab == index, onClick = { onTab(index) },
                        icon = { PuenteIcon(icons[index]) }, label = { Text(stringResource(label), maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis) })
                }
            }
        },
    ) { insets ->
        Box(Modifier.fillMaxSize().padding(insets).consumeWindowInsets(insets)) {
            when (tab) {
                0 -> HistoryScreen(header = {
                    if (!model.paired) EmptyContent(R.drawable.ic_pc, stringResource(R.string.link_pc_to_start))
                    ManualActions(model.paired && !model.busy, openAction)
                    ConnectionNotice(model)
                    updates.available?.let {
                        ActionButton(stringResource(R.string.update_available, it.versionName), R.drawable.ic_down,
                            { onTab(StartTab.SETTINGS) }, Modifier.fillMaxWidth())
                    }
                    if (!model.paired) ActionButton(stringResource(R.string.link_a_pc), R.drawable.ic_plus, { onTab(StartTab.DEVICES) }, primary = true)
                })
                1 -> FilesPanel(model, openAction)
                2 -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(18.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp)) { EquipmentPanel(model, refresh) }
                3 -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(18.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    SettingsPanel(model, updates, shizuku, toggleNotification, refresh, install, fileNotificationsAllowed, requestFileNotifications)
                }
            }
        }
    }
}

@Composable private fun DashboardHeader(model: SettingsViewModel) {
    Column(Modifier.windowInsetsPadding(WindowInsets.statusBars)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 8.dp),
            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Surface(shape = androidx.compose.foundation.shape.RoundedCornerShape(8.dp),
                color = MaterialTheme.colorScheme.surfaceContainer) {
                Box(Modifier.size(40.dp), contentAlignment = androidx.compose.ui.Alignment.Center) {
                    PuenteIcon(R.drawable.ic_clipboard)
                }
            }
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.app_name), style = MaterialTheme.typography.titleLarge,
                    maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                Text(if (model.paired) model.pcName + " · " + stringResource(
                    if (model.connected) R.string.home_connected else R.string.home_not_checked)
                    else stringResource(R.string.link_pc_to_start),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
            }
        }
        if (model.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
    }
}

@Composable private fun ManualActions(enabled: Boolean, action: (String) -> Unit) {
    val attach = stringResource(R.string.action_send_file)
    val send = stringResource(R.string.home_send_copy)
    val receive = stringResource(R.string.receive)
    if (androidx.compose.ui.platform.LocalDensity.current.fontScale >= 1.5f) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            ActionButton(send, R.drawable.ic_up, { action(QuickNotification.SEND) }, Modifier.fillMaxWidth(), enabled, primary = true)
            ActionButton(receive, R.drawable.ic_down, { action(QuickNotification.RECEIVE) }, Modifier.fillMaxWidth(), enabled)
            ActionButton(attach, R.drawable.ic_attach, { action(QuickNotification.ATTACH) }, Modifier.fillMaxWidth(), enabled)
        }
        return
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        ActionButton(send, R.drawable.ic_up, { action(QuickNotification.SEND) }, Modifier.weight(1f), enabled, primary = true, stacked = true)
        ActionButton(receive, R.drawable.ic_down, { action(QuickNotification.RECEIVE) }, Modifier.weight(1f), enabled, stacked = true)
        ActionButton(attach, R.drawable.ic_attach, { action(QuickNotification.ATTACH) }, Modifier.weight(1f), enabled, stacked = true)
    }
}

@Composable private fun ConnectionNotice(model: SettingsViewModel) {
    var details by remember { mutableStateOf(false) }
    if (model.paired && !model.busy && !model.connected) {
        TextButton(onClick = { details = true }) {
            PuenteIcon(R.drawable.ic_warning); Spacer(Modifier.width(8.dp)); Text(stringResource(R.string.check_connection))
        }
    }
    if (details) AlertDialog(onDismissRequest = { details = false },
        icon = { PuenteIcon(R.drawable.ic_warning) }, title = { Text(stringResource(R.string.pc_connection_title)) },
        text = { Text(model.status) },
        confirmButton = { TextButton(onClick = { details = false; model.reconnect() }) { Text(stringResource(R.string.retry)) } },
        dismissButton = { TextButton(onClick = { details = false }) { Text(stringResource(R.string.close)) } })
}
