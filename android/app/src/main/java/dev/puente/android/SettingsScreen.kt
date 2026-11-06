package dev.puente.android

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.unit.dp

@Composable
fun SettingsPanel(model: SettingsViewModel, updates: AppUpdateViewModel, shizuku: ShizukuSettingsModel,
                  toggleNotification: (Boolean) -> Unit, refresh: () -> Unit, install: () -> Unit,
                  fileNotificationsAllowed: Boolean = true, requestFileNotifications: () -> Unit = {}) {
    val context = LocalContext.current
    var guide by remember { mutableStateOf(false) }
    var help by remember { mutableStateOf(false) }
    var licenses by rememberSaveable { mutableStateOf(false) }
    var explanation by remember { mutableStateOf<Pair<Int, String>?>(null) }
    val syncStates by SyncStatusStore.get(context).states.collectAsStateWithLifecycle()
    val receiving = syncStates["${model.activePcId}:${SyncDirection.FROM_PC.name}"] ?: DirectionState()
    Text(stringResource(R.string.tab_settings), style = MaterialTheme.typography.titleLarge)
    SettingsGroup(stringResource(R.string.settings_sync)) {
        if (model.paired) SyncStatusPanel(context, model.activePcId,
            model.automaticReceive, shizuku.selected, onSettings = {})
        if (!model.paired) Text(stringResource(R.string.link_before_automation), style = MaterialTheme.typography.bodySmall)
        SettingControl(stringResource(R.string.settings_auto_receive), R.drawable.ic_down, model.automaticReceive,
            enabled = model.paired, onInfo = { explanation = R.string.settings_auto_receive to context.getString(R.string.auto_receive_info) }) {
            model.automaticReceiveEnabled(it); refresh()
        }
        if (model.automaticReceive && model.paired) ReceiverStatus(receiving) {
            explanation = R.string.settings_auto_receive to receiving.error.ifBlank { context.getString(R.string.auto_receive_info) }
        }
        SettingControl(stringResource(R.string.settings_send_shizuku), R.drawable.ic_up, shizuku.selected,
            enabled = model.paired, onInfo = { explanation = R.string.settings_send_shizuku to context.getString(R.string.auto_send_info) }) { enabled ->
            if (enabled) { shizuku.enable(); if (!ShizukuAccess.running() || !ShizukuAccess.granted()) guide = true }
            else shizuku.select(false)
            refresh()
        }
        ActionRow(stringResource(R.string.shizuku_setup), R.drawable.ic_settings, { shizuku.refresh(); guide = true })
        ConnectionCheckControl(model)
    }
    SettingsGroup(stringResource(R.string.settings_history_notices)) {
        HistorySettings()
        SettingControl(stringResource(R.string.settings_file_offers), R.drawable.ic_folder, model.fileOffers,
            enabled = model.paired, onInfo = { explanation = R.string.settings_file_offers to context.getString(R.string.file_alerts_info) }) {
            model.fileOffersEnabled(it); refresh()
            if (it) requestFileNotifications()
        }
        if (model.fileOffers && model.paired && !fileNotificationsAllowed) {
            ActionRow(stringResource(R.string.notification_permission_needed), R.drawable.ic_warning, requestFileNotifications)
        }
        SettingRow(stringResource(R.string.settings_notification_buttons), R.drawable.ic_phone, model.notification, toggleNotification)
    }
    UpdateSettings(updates, install)
    ActionRow(stringResource(R.string.help), R.drawable.ic_help, { help = true })
    ActionRow(stringResource(R.string.licenses_title), R.drawable.ic_file, { licenses = true })
    if (licenses) OpenSourceLicensesScreen(onDismiss = { licenses = false })
    explanation?.let { (title, message) -> AlertDialog(onDismissRequest = { explanation = null },
        title = { Text(stringResource(title)) }, text = { Text(message) },
        confirmButton = { TextButton(onClick = { explanation = null }) { Text(stringResource(R.string.close)) } }) }
    if (guide) ShizukuGuide(context, shizuku, model.activePcId, onDismiss = { guide = false })
    if (help) AlertDialog(onDismissRequest = { help = false }, title = { Text(stringResource(R.string.help)) },
        text = {
            Column(Modifier.heightIn(max = 400.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                HelpTopic(R.drawable.ic_clipboard, R.string.help_clipboard_title, R.string.help_clipboard)
                HelpTopic(R.drawable.ic_folder, R.string.help_files_title, R.string.help_files)
                HelpTopic(R.drawable.ic_pin, R.string.help_history_title, R.string.help_history)
                HelpTopic(R.drawable.ic_down, R.string.help_updates_title, R.string.help_updates)
            }
        }, confirmButton = { TextButton(onClick = { help = false }) { Text(stringResource(R.string.close)) } })
}

@Composable
fun UpdateSettings(updates: AppUpdateViewModel, install: () -> Unit) {
    SettingsGroup(stringResource(R.string.updates_title_version, updates.installedVersion)) {
        SettingRow(stringResource(R.string.updates_auto), R.drawable.ic_refresh, updates.automatic) { updates.automaticEnabled(it); if (it) updates.autoCheck() }
        if (updates.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        if (updates.downloaded != null) ActionButton(stringResource(R.string.install), R.drawable.ic_down, install, enabled = !updates.busy, primary = true)
        else if (updates.available != null) ActionButton(stringResource(R.string.download), R.drawable.ic_down, updates::download, enabled = !updates.busy, primary = true)
        Text(updates.message, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        ActionRow(stringResource(R.string.check_updates), R.drawable.ic_refresh, { updates.check() }, enabled = !updates.busy)
    }
}

@Composable private fun SettingsGroup(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)) {
            Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 4.dp), content = content)
        }
    }
}

@Composable private fun ShizukuGuide(context: Context, shizuku: ShizukuSettingsModel, pcId: String, onDismiss: () -> Unit) {
    val installed = context.packageManager.getLaunchIntentForPackage("moe.shizuku.privileged.api") != null
    val running = ShizukuAccess.running()
    val granted = ShizukuAccess.granted()
    val states by SyncStatusStore.get(context).states.collectAsStateWithLifecycle()
    val reading = states["$pcId:${SyncDirection.TO_PC.name}"]
    val clipboard = reading?.phase in setOf(SyncPhase.READY, SyncPhase.WAITING, SyncPhase.TRANSFERRING, SyncPhase.SEARCHING)
    var steps by remember { mutableStateOf(false) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Shizuku") },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                GuideStep(stringResource(R.string.shizuku_step_installed, stringResource(if (installed) R.string.word_yes else R.string.word_no)), installed)
                GuideStep(stringResource(R.string.shizuku_step_running, stringResource(if (running) R.string.word_yes else R.string.word_no)), running)
                GuideStep(stringResource(R.string.shizuku_step_permission, stringResource(if (granted) R.string.word_granted else R.string.word_pending)), granted)
                GuideStep(stringResource(R.string.shizuku_step_clipboard, stringResource(if (clipboard) R.string.word_working else R.string.word_not_yet)), clipboard)
                ActionRow(stringResource(R.string.shizuku_open_or_install), R.drawable.ic_phone, {
                    val intent = context.packageManager.getLaunchIntentForPackage("moe.shizuku.privileged.api")
                        ?: Intent(Intent.ACTION_VIEW, Uri.parse("https://shizuku.rikka.app/download/"))
                    runCatching { context.startActivity(intent) }
                })
                ActionRow(stringResource(R.string.shizuku_authorize), R.drawable.ic_check, shizuku::enable)
                ActionRow(stringResource(if (steps) R.string.shizuku_hide_steps else R.string.shizuku_show_steps), R.drawable.ic_help, { steps = !steps })
                if (steps) {
                    Text(stringResource(R.string.shizuku_guide))
                    ActionRow(stringResource(R.string.developer_options), R.drawable.ic_settings, {
                        runCatching { context.startActivity(Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)) }
                    })
                }
                Text(shizuku.status, style = MaterialTheme.typography.bodySmall)
            }
        }, confirmButton = { TextButton(onClick = { shizuku.refresh(); onDismiss() }) { Text(stringResource(R.string.done)) } })
}

@Composable private fun GuideStep(label: String, complete: Boolean) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        PuenteIcon(if (complete) R.drawable.ic_check else R.drawable.ic_clock, stringResource(if (complete) R.string.step_complete else R.string.step_pending))
        Text(label)
    }
}
