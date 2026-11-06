package dev.puente.android

import android.content.Context
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay
import java.text.DateFormat
import java.util.Date

@Composable
fun SyncStatusPanel(context: Context, pcId: String, receiving: Boolean, sending: Boolean, onSettings: () -> Unit) {
    val store = SyncStatusStore.get(context)
    val states by store.states.collectAsStateWithLifecycle()
    val from = states["$pcId:${SyncDirection.FROM_PC.name}"] ?: store.state(pcId, SyncDirection.FROM_PC)
    val to = states["$pcId:${SyncDirection.TO_PC.name}"] ?: store.state(pcId, SyncDirection.TO_PC)
    val delivery = latestDelivery(listOf(from, to))
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    var details by remember(pcId) { mutableStateOf<String?>(null) }
    var directionDetails by remember(pcId) { mutableStateOf<Pair<SyncDirection, DirectionState>?>(null) }
    LaunchedEffect(Unit) { while (true) { now = System.currentTimeMillis(); delay(30_000) } }

    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        DirectionCard(stringResource(R.string.receive), SyncDirection.FROM_PC, from, receiving, Modifier.weight(1f)) {
            directionDetails = SyncDirection.FROM_PC to if (receiving) from else from.progress(SyncPhase.DISABLED)
        }
        DirectionCard(if (sending) "Shizuku" else stringResource(R.string.settings_manual), SyncDirection.TO_PC, to, sending, Modifier.weight(1f)) {
            directionDetails = SyncDirection.TO_PC to if (sending) to else to.progress(SyncPhase.DISABLED)
        }
        Surface(onClick = {
            details = delivery?.let { "${context.getString(contentKind(it.kind)).replaceFirstChar(Char::uppercase)} · ${DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date(it.deliveredAt))}" }
                ?: context.getString(R.string.no_auto_deliveries)
        }, shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.weight(1f)) {
            Column(Modifier.heightIn(min = 92.dp).padding(horizontal = 6.dp, vertical = 12.dp),
                horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    PuenteIcon(R.drawable.ic_clock, modifier = Modifier.size(18.dp))
                    Text(delivery?.let { relativeTimeLabel(relativeDeliveryTime(it.deliveredAt, now)) } ?: "—", style = MaterialTheme.typography.titleMedium)
                }
                Text(stringResource(R.string.last_delivery_label), style = MaterialTheme.typography.labelSmall, maxLines = 2)
            }
        }
    }
    details?.let { message -> AlertDialog(onDismissRequest = { details = null }, title = { Text(stringResource(R.string.settings_sync)) },
        text = { Text(message) }, confirmButton = { TextButton(onClick = { details = null; onSettings() }) { Text(stringResource(R.string.tab_settings)) } },
        dismissButton = { TextButton(onClick = { details = null }) { Text(stringResource(R.string.close)) } }) }
    directionDetails?.let { (direction, state) -> DirectionDetails(direction, state,
        onDismiss = { directionDetails = null }, onSettings = { directionDetails = null; onSettings() }) }
}

@Composable private fun DirectionDetails(direction: SyncDirection, state: DirectionState, onDismiss: () -> Unit, onSettings: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(if (direction == SyncDirection.FROM_PC) R.string.receive else R.string.send)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    SyncBadge(syncIndicator(state)); Text(stringResource(state.phase.label))
                }
                if (state.error.isNotEmpty()) Text(state.error, color = MaterialTheme.colorScheme.error)
                if (state.verifiedAt > 0) SyncDetailRow(R.drawable.ic_pc, stringResource(R.string.pc_checked), state.verifiedAt)
                if (state.deliveredAt > 0) SyncDetailRow(R.drawable.ic_copy, stringResource(R.string.last_delivery_kind, stringResource(contentKind(state.kind))), state.deliveredAt)
            }
        }, confirmButton = { TextButton(onClick = onSettings) { Text(stringResource(R.string.tab_settings)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.close)) } })
}

@Composable private fun SyncDetailRow(icon: Int, label: String, time: Long) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        PuenteIcon(icon)
        Column {
            Text(label, style = MaterialTheme.typography.labelMedium)
            Text(DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date(time)), style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable private fun DirectionCard(label: String, direction: SyncDirection, state: DirectionState,
                                     enabled: Boolean, modifier: Modifier, onClick: () -> Unit) {
    val signal = if (enabled) syncIndicator(state) else SyncIndicator.DISABLED
    val first = if (direction == SyncDirection.FROM_PC) R.drawable.ic_pc else R.drawable.ic_phone
    val second = if (direction == SyncDirection.FROM_PC) R.drawable.ic_phone else R.drawable.ic_pc
    val description = "${stringResource(direction.label)}: ${stringResource(if (enabled) state.phase.label else R.string.settings_manual)}"
    Surface(onClick, modifier.semantics { contentDescription = description },
        shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceContainer) {
        Column(Modifier.heightIn(min = 92.dp).padding(horizontal = 6.dp, vertical = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                PuenteIcon(first, modifier = Modifier.size(20.dp))
                PuenteIcon(R.drawable.ic_right, modifier = Modifier.size(12.dp))
                PuenteIcon(second, modifier = Modifier.size(20.dp))
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(label, Modifier.weight(1f, fill = false), style = MaterialTheme.typography.labelMedium,
                    maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                SyncBadge(signal)
            }
        }
    }
}

@Composable private fun SyncBadge(signal: SyncIndicator) {
    when (signal) {
        SyncIndicator.BUSY -> CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
        SyncIndicator.ACTIVE -> Surface(color = Color(0xFF244B35), contentColor = Color(0xFFAAF0C1), shape = RoundedCornerShape(12.dp)) {
            PuenteIcon(R.drawable.ic_check, stringResource(R.string.status_active), Modifier.padding(3.dp).size(16.dp))
        }
        SyncIndicator.ATTENTION -> Icon(painter = androidx.compose.ui.res.painterResource(R.drawable.ic_warning),
            contentDescription = stringResource(R.string.needs_attention), tint = MaterialTheme.colorScheme.error, modifier = Modifier.size(16.dp))
        SyncIndicator.DISABLED -> PuenteIcon(R.drawable.ic_pause, stringResource(R.string.phase_disabled), Modifier.size(16.dp))
    }
}

@Composable private fun relativeTimeLabel(time: RelativeTime): String = when (time.unit) {
    RelativeUnit.NOW -> stringResource(R.string.time_now)
    RelativeUnit.MINUTES -> stringResource(R.string.time_minutes, time.amount)
    RelativeUnit.HOURS -> stringResource(R.string.time_hours, time.amount)
    RelativeUnit.DAYS -> stringResource(R.string.time_days, time.amount)
}
