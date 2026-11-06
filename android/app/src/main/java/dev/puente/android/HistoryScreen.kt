package dev.puente.android

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.DateFormat
import java.util.Date

@Composable
fun HistorySettings(store: HistoryStore = HistoryStore.get(LocalContext.current)) {
    val enabled by store.enabled.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    SettingRow(stringResource(R.string.history_save), R.drawable.ic_clipboard, enabled) { value -> scope.launch { store.setEnabled(value) } }
}

@Composable
fun HistoryScreen(store: HistoryStore = HistoryStore.get(LocalContext.current), header: @Composable () -> Unit = {}) {
    val entries by store.entries.collectAsStateWithLifecycle()
    val message by store.message.collectAsStateWithLifecycle()
    val enabled by store.enabled.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var confirmClear by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<HistoryEntry?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    fun perform(action: suspend () -> Unit) {
        scope.launch {
            try { action() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { error = context.getString(R.string.history_action_failed) }
        }
    }
    LaunchedEffect(store) {
        while (true) {
            try { store.refresh() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { error = context.getString(R.string.history_open_failed) }
            delay(60_000)
        }
    }
    HistoryFeed(entries,
        onCopy = { item -> perform { if (!store.copyToClipboard(context, item.id)) error = context.getString(R.string.history_item_gone) } },
        onPin = { item -> perform { store.pin(item.id, !item.pinned) } },
        onDelete = { item -> perform { store.delete(item.id) } },
        onOpen = { selected = it }, onClear = { confirmClear = true }, thumbnail = store::thumbnail,
        header = {
            header()
            if (!enabled) Text(stringResource(R.string.history_disabled), style = MaterialTheme.typography.labelMedium)
            (error ?: message?.let { historyNotice(it) })?.let { reason ->
                var show by remember { mutableStateOf(false) }
                TextButton(onClick = { show = true }) {
                    PuenteIcon(R.drawable.ic_warning); Spacer(Modifier.width(8.dp)); Text(stringResource(R.string.history_check))
                }
                if (show) AlertDialog(onDismissRequest = { show = false }, title = { Text(stringResource(R.string.history_title)) },
                    text = { Text(reason) }, confirmButton = { TextButton(onClick = { show = false; error = null }) { Text(stringResource(R.string.close)) } })
            }
        })
    selected?.let { item -> HistoryPreview(item, store, onDismiss = { selected = null }, onCopy = {
        perform { if (!store.copyToClipboard(context, item.id)) error = context.getString(R.string.history_item_gone) }
        selected = null
    }) }
    if (confirmClear) AlertDialog(onDismissRequest = { confirmClear = false },
        title = { Text(stringResource(R.string.clear_recent_title)) }, text = { Text(stringResource(R.string.clear_recent_message)) },
        confirmButton = { TextButton(onClick = { confirmClear = false; perform { store.clearRecent() } }) { Text(stringResource(R.string.clear_recent_confirm)) } },
        dismissButton = { TextButton(onClick = { confirmClear = false }) { Text(stringResource(R.string.cancel)) } })
}

/** A single bounded list owns scrolling, including the status/actions header. */
@Composable
fun HistoryFeed(entries: List<HistoryEntry>, onCopy: (HistoryEntry) -> Unit, onPin: (HistoryEntry) -> Unit,
                onDelete: (HistoryEntry) -> Unit, onOpen: (HistoryEntry) -> Unit, onClear: () -> Unit,
                modifier: Modifier = Modifier, thumbnail: suspend (String) -> Bitmap? = { null },
                header: @Composable () -> Unit = {}) {
    var requestedPage by rememberSaveable { mutableIntStateOf(0) }
    val page = remember(entries, requestedPage) { HistoryPaging.create(entries, requestedPage) }
    val listState = rememberLazyListState()
    LaunchedEffect(page.index) {
        requestedPage = page.index
        listState.scrollToItem(0)
    }
    Column(modifier.fillMaxSize()) {
    LazyColumn(Modifier.weight(1f).testTag("history-feed"), state = listState,
        contentPadding = PaddingValues(start = 18.dp, end = 18.dp, top = 8.dp, bottom = 20.dp),
        verticalArrangement = Arrangement.spacedBy(0.dp)) {
        item(key = "header") { Column(verticalArrangement = Arrangement.spacedBy(12.dp)) { header() } }
        item(key = "controls") {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.history_title), Modifier.weight(1f), style = MaterialTheme.typography.titleLarge)
                IconButton(onClick = onClear, enabled = entries.any { !it.pinned }) { PuenteIcon(R.drawable.ic_trash, stringResource(R.string.clear_recent_confirm)) }
            }
        }
        if (entries.isEmpty()) item(key = "empty") { EmptyContent(R.drawable.ic_clipboard, stringResource(R.string.history_empty)) }
        items(page.entries, key = { "entry:${it.id}" }, contentType = { it.kind }) { item ->
            HistoryCard(item, thumbnail, onCopy, onPin, onDelete, onOpen)
        }
    }
    if (entries.isNotEmpty()) {
        HorizontalDivider()
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween) {
            TextButton(onClick = { requestedPage = page.index - 1 }, enabled = page.index > 0,
                modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("history-previous")) { Text(stringResource(R.string.history_previous), maxLines = 1, overflow = TextOverflow.Ellipsis) }
            Text(stringResource(R.string.history_page, page.index + 1, page.total),
                style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = { requestedPage = page.index + 1 }, enabled = page.index + 1 < page.total,
                modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("history-next")) { Text(stringResource(R.string.history_next), maxLines = 1, overflow = TextOverflow.Ellipsis) }
        }
    }
    }
}

@Composable private fun HistoryCard(item: HistoryEntry, thumbnail: suspend (String) -> Bitmap?,
                                   onCopy: (HistoryEntry) -> Unit, onPin: (HistoryEntry) -> Unit,
                                   onDelete: (HistoryEntry) -> Unit, onOpen: (HistoryEntry) -> Unit) {
    var menu by remember { mutableStateOf(false) }
    val summary = remember(item.content) { HistoryPaging.summary(item.content) }
    val date = remember(item.createdAt) { DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date(item.createdAt)) }
    Surface(onClick = { onOpen(item) }, modifier = Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.background) {
        Column {
            Row(Modifier.fillMaxWidth().heightIn(min = 88.dp).padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                if (item.kind == HistoryKind.IMAGE) {
                    HistoryThumbnail(item, thumbnail, Modifier.size(40.dp))
                    Spacer(Modifier.width(10.dp))
                }
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(if (item.kind == HistoryKind.TEXT) summary else stringResource(R.string.history_image_size, LocalContext.current.fileSize(item.imageBytes)),
                        maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
                    Text(date,
                        style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                IconToggleButton(checked = item.pinned, onCheckedChange = { onPin(item) }) {
                    PuenteIcon(R.drawable.ic_pin, stringResource(if (item.pinned) R.string.unpin else R.string.pin))
                }
                IconButton(onClick = { onCopy(item) }) {
                    PuenteIcon(R.drawable.ic_copy, stringResource(R.string.copy))
                }
                Box {
                    IconButton(onClick = { menu = true }) { PuenteIcon(R.drawable.ic_more, stringResource(R.string.more_options)) }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        DropdownMenuItem(text = { Text(stringResource(R.string.history_view_full)) }, onClick = { menu = false; onOpen(item) }, leadingIcon = { PuenteIcon(R.drawable.ic_clipboard) })
                        DropdownMenuItem(text = { Text(stringResource(R.string.delete)) }, onClick = { menu = false; onDelete(item) }, leadingIcon = { PuenteIcon(R.drawable.ic_trash) })
                    }
                }
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        }
    }
}

@Composable private fun HistoryThumbnail(item: HistoryEntry, thumbnail: suspend (String) -> Bitmap?, modifier: Modifier) {
    val bitmap by produceState<Bitmap?>(null, item.id) { value = runCatching { thumbnail(item.id) }.getOrNull() }
    bitmap?.let { Image(it.asImageBitmap(), stringResource(R.string.history_image_description), modifier, contentScale = ContentScale.Crop) }
        ?: PuenteIcon(R.drawable.ic_image, stringResource(R.string.history_image_description), modifier)
}

@Composable private fun HistoryPreview(item: HistoryEntry, store: HistoryStore, onDismiss: () -> Unit, onCopy: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(if (item.kind == HistoryKind.TEXT) R.string.kind_text else R.string.kind_image)) },
        text = {
            Column(Modifier.heightIn(max = 400.dp).verticalScroll(rememberScrollState())) {
                if (item.kind == HistoryKind.TEXT) SelectionContainer { Text(item.content) }
                else HistoryThumbnail(item, store::thumbnail, Modifier.fillMaxWidth().height(240.dp))
            }
        }, confirmButton = { TextButton(onClick = onCopy) { PuenteIcon(R.drawable.ic_copy); Spacer(Modifier.width(6.dp)); Text(stringResource(R.string.copy)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.close)) } })
}

@Composable
private fun historyNotice(notice: HistoryNotice): String = when (notice) {
    HistoryNotice.TEXT_TOO_LONG -> stringResource(R.string.history_text_too_long)
    HistoryNotice.IMAGE_TOO_LARGE -> stringResource(R.string.history_image_too_large)
    HistoryNotice.PIN_LIMIT -> pluralStringResource(R.plurals.history_pin_limit, HistoryPolicy.MAX_PINS, HistoryPolicy.MAX_PINS)
    HistoryNotice.DAMAGED -> stringResource(R.string.history_damaged)
}
