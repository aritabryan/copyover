package dev.puente.android

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable internal fun ConnectionCheckControl(model: SettingsViewModel) {
    val feedback by model.connectionCheck.feedback.collectAsStateWithLifecycle()
    ActionRow(stringResource(if (feedback.phase == ConnectionCheckPhase.CHECKING) R.string.checking_connection else R.string.check_connection),
        R.drawable.ic_refresh, model::checkConnection, enabled = model.paired && !model.busy)
    ConnectionCheckResult(feedback, model.connectionCheck::dismiss, model::checkConnection)
}

@Composable internal fun ConnectionCheckResult(feedback: ConnectionCheckFeedback, onDismiss: () -> Unit, onRetry: () -> Unit) {
    if (feedback.phase != ConnectionCheckPhase.SUCCESS && feedback.phase != ConnectionCheckPhase.ERROR) return
    val success = feedback.phase == ConnectionCheckPhase.SUCCESS
    AlertDialog(onDismissRequest = onDismiss,
        icon = { PuenteIcon(if (success) R.drawable.ic_check else R.drawable.ic_warning) },
        title = { Text(stringResource(R.string.connection_check_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (success) {
                    Text(feedback.pcName, style = MaterialTheme.typography.titleMedium)
                    Text(stringResource(R.string.connection_check_success))
                } else Text(feedback.message)
            }
        }, confirmButton = {
            TextButton(onClick = { onDismiss(); if (!success) onRetry() }) {
                Text(stringResource(if (success) R.string.done else R.string.retry))
            }
        }, dismissButton = { if (!success) TextButton(onClick = onDismiss) { Text(stringResource(R.string.close)) } })
}

@Composable internal fun ReceiverStatus(state: DirectionState, onClick: () -> Unit) {
    val signal = syncIndicator(state)
    val label = when (signal) {
        SyncIndicator.ACTIVE -> R.string.auto_receive_ready
        SyncIndicator.ATTENTION -> R.string.auto_receive_problem
        SyncIndicator.BUSY -> R.string.checking_connection
        SyncIndicator.DISABLED -> R.string.auto_receive_waiting_start
    }
    TextButton(onClick = onClick) {
        if (signal == SyncIndicator.BUSY) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
        else PuenteIcon(if (signal == SyncIndicator.ACTIVE) R.drawable.ic_check else R.drawable.ic_help)
        Spacer(Modifier.width(8.dp)); Text(stringResource(label), style = MaterialTheme.typography.labelMedium)
    }
}

@Composable internal fun HelpTopic(icon: Int, title: Int, body: Int) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PuenteIcon(icon); Text(stringResource(title), style = MaterialTheme.typography.titleSmall)
        }
        Text(stringResource(body), style = MaterialTheme.typography.bodyMedium)
    }
}
