package dev.puente.android

import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** One third-party library as described by the generated aboutlibraries.json. */
data class LibraryNotice(val id: String, val name: String, val version: String,
                         val licenseNames: List<String>, val licenseText: String)

/** Everything shown on the licenses screen; [rust] is null when the build did not bundle it. */
data class LicenseNotices(val copyOver: String, val libraries: List<LibraryNotice>, val rust: String?)

object LicenseCatalog {
    const val COPYOVER_ASSET = "licenses/copyover.txt"
    const val RUST_ASSET = "licenses-rust.txt"

    /** Parses the AboutLibraries export, sorted by name. Unknown license ids are shown as-is. */
    fun parse(json: String): List<LibraryNotice> {
        val root = JSONObject(json)
        val licenses = root.optJSONObject("licenses") ?: JSONObject()
        val libraries = root.optJSONArray("libraries") ?: return emptyList()
        return (0 until libraries.length()).map { index ->
            val library = libraries.getJSONObject(index)
            val id = library.optString("uniqueId")
            val keys = library.optJSONArray("licenses")
                ?.let { array -> (0 until array.length()).map { array.getString(it) } }.orEmpty()
            val known = keys.mapNotNull { licenses.optJSONObject(it) }
            LibraryNotice(
                id = id,
                name = library.optString("name").ifBlank { id },
                version = library.optString("artifactVersion"),
                licenseNames = keys.map { key -> licenses.optJSONObject(key)?.optString("name")?.ifBlank { null } ?: key },
                licenseText = known.joinToString("\n\n") { it.optString("content").trim() }.trim(),
            )
        }.sortedWith(compareBy(String.CASE_INSENSITIVE_ORDER) { it.name })
    }

    fun load(context: Context): LicenseNotices {
        fun asset(name: String) = runCatching { context.assets.open(name).bufferedReader().use { it.readText() } }.getOrNull()
        val json = context.resources.openRawResource(R.raw.aboutlibraries).bufferedReader().use { it.readText() }
        return LicenseNotices(
            copyOver = asset(COPYOVER_ASSET).orEmpty().trim(),
            libraries = parse(json),
            rust = asset(RUST_ASSET)?.trim()?.ifBlank { null },
        )
    }

    /** Lines indented this much are titles centered with spaces for an 80-column page. */
    private const val CENTERED_INDENT = 10

    /**
     * Splits long texts so a lazy list only lays out the paragraphs on screen. Titles centered
     * with spaces are moved to the left margin, since they would wrap badly on a phone.
     */
    fun paragraphs(text: String): List<String> =
        text.replace("\r\n", "\n").split(Regex("\n{2,}"))
            .map { part -> part.lines().joinToString("\n") { line ->
                if (line.length - line.trimStart().length >= CENTERED_INDENT) line.trim() else line.trimEnd() } }
            .map { it.trimEnd() }.filter { it.isNotBlank() }
}

@Composable
fun OpenSourceLicensesScreen(onDismiss: () -> Unit) {
    val context = LocalContext.current
    val notices by produceState<Result<LicenseNotices>?>(null) {
        value = withContext(Dispatchers.IO) { runCatching { LicenseCatalog.load(context.applicationContext) } }
    }
    var expanded by rememberSaveable { mutableStateOf<String?>(null) }
    fun toggle(key: String) { expanded = if (expanded == key) null else key }
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxSize()) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onDismiss) { PuenteIcon(R.drawable.ic_close, stringResource(R.string.close)) }
                    Text(stringResource(R.string.licenses_title), style = MaterialTheme.typography.titleLarge)
                }
                HorizontalDivider()
                val loaded = notices
                when {
                    loaded == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                    loaded.isFailure -> EmptyContent(R.drawable.ic_warning, stringResource(R.string.licenses_unavailable))
                    else -> LicenseList(loaded.getOrThrow(), expanded, ::toggle)
                }
            }
        }
    }
}

@Composable private fun LicenseList(notices: LicenseNotices, expanded: String?, toggle: (String) -> Unit) {
    val rustTitle = stringResource(R.string.licenses_rust_entry)
    val rustDetail = stringResource(R.string.licenses_rust_detail)
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp, 12.dp)) {
        item {
            Text(stringResource(R.string.licenses_intro), style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(bottom = 8.dp))
        }
        notice("copyover", "CopyOver", "MIT", notices.copyOver, expanded, toggle)
        item { SectionTitle(stringResource(R.string.licenses_android_libraries)) }
        notices.libraries.forEach { library ->
            val detail = listOf(library.version, library.licenseNames.joinToString(", "))
                .filter { it.isNotBlank() }.joinToString(" · ")
            notice("lib:${library.id}", library.name, detail, library.licenseText, expanded, toggle)
        }
        notices.rust?.let { rust ->
            item { SectionTitle(stringResource(R.string.licenses_rust_libraries)) }
            notice("rust", rustTitle, rustDetail, rust, expanded, toggle)
        }
    }
}

private fun LazyListScope.notice(key: String, title: String, detail: String, text: String,
                                 expanded: String?, toggle: (String) -> Unit) {
    val open = expanded == key
    item(key = key) {
        Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).clickable(role = Role.Button) { toggle(key) }.padding(vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.bodyLarge)
                if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            PuenteIcon(R.drawable.ic_chevron,
                stringResource(if (open) R.string.licenses_hide_text else R.string.licenses_show_text),
                Modifier.rotate(if (open) 180f else 0f))
        }
    }
    if (open) {
        val parts = LicenseCatalog.paragraphs(text)
        if (parts.isEmpty()) item(key = "$key:empty") {
            Text(stringResource(R.string.licenses_text_missing), style = MaterialTheme.typography.bodySmall)
        }
        items(parts.size, key = { "$key:$it" }) { index ->
            SelectionContainer(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer)) {
                Text(parts[index], Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                    style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace))
            }
        }
    }
}

@Composable private fun SectionTitle(title: String) {
    Text(title, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 16.dp, bottom = 4.dp))
}
