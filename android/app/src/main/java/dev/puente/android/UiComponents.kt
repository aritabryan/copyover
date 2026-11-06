package dev.puente.android

import androidx.annotation.DrawableRes
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

@Composable
fun PuenteIcon(@DrawableRes icon: Int, description: String? = null, modifier: Modifier = Modifier) {
    Icon(painterResource(icon), description, modifier.size(22.dp))
}

@Composable
fun ActionButton(label: String, @DrawableRes icon: Int, onClick: () -> Unit,
                 modifier: Modifier = Modifier, enabled: Boolean = true, primary: Boolean = false, stacked: Boolean = false) {
    val padding = if (stacked) PaddingValues(horizontal = 6.dp, vertical = 10.dp) else PaddingValues(horizontal = 12.dp)
    if (primary) Button(onClick, modifier.heightIn(min = 48.dp), enabled = enabled,
        shape = RoundedCornerShape(8.dp), contentPadding = padding) {
        ActionLabel(label, icon, stacked)
    } else OutlinedButton(onClick, modifier.heightIn(min = 48.dp), enabled = enabled,
        shape = RoundedCornerShape(8.dp), contentPadding = padding) {
        ActionLabel(label, icon, stacked)
    }
}

@Composable private fun ActionLabel(label: String, icon: Int, stacked: Boolean) {
    if (stacked) Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        PuenteIcon(icon)
        Text(label, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.labelMedium,
            textAlign = TextAlign.Center)
    } else Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        PuenteIcon(icon); Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
fun SettingRow(label: String, @DrawableRes icon: Int, checked: Boolean, change: (Boolean) -> Unit) {
    SettingControl(label, icon, checked, change = change)
}

@Composable
fun SettingControl(label: String, @DrawableRes icon: Int, checked: Boolean, enabled: Boolean = true,
                   onInfo: (() -> Unit)? = null, change: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 60.dp).toggleable(checked, enabled = enabled, role = Role.Switch, onValueChange = change),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        PuenteIcon(icon)
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
        if (onInfo != null) IconButton(onClick = onInfo) { PuenteIcon(R.drawable.ic_help, stringResource(R.string.setting_help_label, label)) }
        Switch(checked, onCheckedChange = null, enabled = enabled)
    }
}

@Composable
fun ActionRow(label: String, @DrawableRes icon: Int, onClick: () -> Unit, enabled: Boolean = true) {
    TextButton(onClick, Modifier.fillMaxWidth().heightIn(min = 52.dp), enabled = enabled,
        contentPadding = PaddingValues(horizontal = 0.dp)) {
        PuenteIcon(icon); Spacer(Modifier.width(12.dp))
        Text(label, Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
        PuenteIcon(R.drawable.ic_right)
    }
}

@Composable
fun EmptyContent(@DrawableRes icon: Int, label: String) {
    Column(Modifier.fillMaxWidth().padding(vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        PuenteIcon(icon, modifier = Modifier.size(40.dp))
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
