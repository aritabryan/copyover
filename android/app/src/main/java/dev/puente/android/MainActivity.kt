package dev.puente.android

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.net.Uri
import android.provider.Settings
import androidx.core.content.FileProvider
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.material3.HorizontalDivider
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

class SettingsViewModel(application: android.app.Application) : AndroidViewModel(application) {
    private val store = ConnectionStore(application)
    private val autoConnection = AutoConnection(application)
    private val discovery = PcDiscovery(application)
    private val repository = TransferRepository(application)
    val connectionCheck = ConnectionCheckController()
    private var pendingConnection: Connection? = null
    private val saved = store.load()
    var paired by mutableStateOf(saved.key.isNotEmpty())
        private set
    var pcName by mutableStateOf(saved.name)
        private set
    var notification by mutableStateOf(store.notificationEnabled)
        private set
    var automaticReceive by mutableStateOf(store.automaticReceive)
        private set
    private fun text(id: Int): String = getApplication<android.app.Application>().getString(id)
    var status by mutableStateOf(text(if (paired) R.string.status_searching_pc else R.string.no_pc_linked))
        private set
    var connected by mutableStateOf(false)
        private set
    var connectionError by mutableStateOf<String?>(null)
        private set
    var busy by mutableStateOf(false)
        private set
    var pcs by mutableStateOf(emptyList<DiscoveredPc>())
        private set
    var savedPcs by mutableStateOf(store.list())
        private set
    var activePcId by mutableStateOf(PcCatalog.identity(saved))
        private set
    var fileOffers by mutableStateOf(store.fileOffers)
        private set
    var code by mutableStateOf("")
        private set
    var needsConfirmation by mutableStateOf(false)
        private set

    init { if (paired) reconnect() else search() }

    fun reconnect() = connect { autoConnection.current() }
    fun checkConnection() = connect(reportCheck = true) { autoConnection.current() }

    fun search() {
        if (busy) return
        connectionError = null
        busy = true
        pcs = emptyList()
        code = ""
        needsConfirmation = false
        pendingConnection = null
        show(text(R.string.status_searching_pcs))
        viewModelScope.launch {
            try {
                pcs = discovery.scan()
                show(text(if (pcs.isEmpty()) R.string.status_no_pcs else R.string.status_choose_pc))
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { connectionFailed(text(R.string.status_search_failed)) }
            finally { busy = false }
        }
    }

    fun pair(pc: DiscoveredPc) {
        if (busy) return
        connectionError = null
        busy = true
        show(text(R.string.status_requesting))
        needsConfirmation = false
        pendingConnection = null
        viewModelScope.launch {
            try {
                pendingConnection = withTimeout(115000) { awaitApproval(pc) }
                needsConfirmation = true
                show(text(R.string.status_pc_accepted))
            } catch (_: TimeoutCancellationException) {
                code = ""
                connectionFailed(text(R.string.status_request_expired))
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                code = ""
                connectionFailed(getApplication<android.app.Application>().describe(error, R.string.status_link_failed))
            } catch (_: UnsatisfiedLinkError) {
                connectionFailed(text(R.string.install_incomplete))
            } finally { busy = false }
        }
    }

    private suspend fun awaitApproval(pc: DiscoveredPc): Connection {
        val ticket = withContext(Dispatchers.IO) { repository.beginPair(pc, Build.MODEL) }
        code = ticket.code
        show(text(R.string.status_accept_on_pc))
        while (true) {
            delay(1000)
            val connection = withContext(Dispatchers.IO) { repository.pollPair(ticket) }
            if (connection != null) return connection
        }
    }

    fun confirmCode() {
        val connection = pendingConnection ?: return
        connect { autoConnection.accept(connection) }
    }

    private fun connect(reportCheck: Boolean = false, operation: suspend () -> Connection) {
        if (busy) return
        connectionError = null
        busy = true
        show(text(R.string.status_searching_pc))
        viewModelScope.launch {
            try {
                val connection = if (reportCheck) connectionCheck.check(operation) { error ->
                    if (error is UnsatisfiedLinkError) text(R.string.install_incomplete)
                    else getApplication<android.app.Application>().describe(error, R.string.status_connect_failed)
                } ?: run {
                    connectionFailed(connectionCheck.feedback.value.message)
                    return@launch
                } else operation()
                pcName = connection.name
                paired = true
                pendingConnection = null
                needsConfirmation = false
                code = ""
                pcs = emptyList()
                reloadPcs()
                status = text(R.string.pc_linked)
                connected = true
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                connectionFailed(getApplication<android.app.Application>().describe(error, R.string.status_connect_failed))
            } catch (_: UnsatisfiedLinkError) {
                connectionFailed(text(R.string.install_incomplete))
            } finally { busy = false }
        }
    }

    fun show(message: String) {
        status = message
        connected = false
    }

    fun notificationEnabled(enabled: Boolean) {
        notification = enabled
        store.notificationEnabled = enabled
    }

    fun automaticReceiveEnabled(enabled: Boolean) {
        store.automaticReceive = enabled
        automaticReceive = enabled
    }

    fun reloadAutomaticReceive() { automaticReceive = store.automaticReceive }

    fun reloadPcs() {
        savedPcs = store.list()
        fileOffers = store.fileOffers
        val active = store.load()
        activePcId = PcCatalog.identity(active)
        pcName = active.name
        paired = active.key.isNotEmpty()
    }
    fun choosePc(id: String) {
        if (busy || id == activePcId) return
        TransferService.cancelForPc(getApplication(), activePcId)
        store.select(id)
        AndroidOfferMonitor.get(getApplication()).clearPending()
        reloadPcs()
        reconnect()
    }
    fun renamePc(id: String, name: String) {
        runCatching { store.rename(id, name); reloadPcs() }.onFailure { connectionFailed(getApplication<android.app.Application>().describe(it, R.string.rename_failed)) }
    }
    private fun connectionFailed(message: String) { show(message); connectionError = message }
    fun forgetPc(id: String) {
        if (busy) return
        val forgotten = store.list().firstOrNull { PcCatalog.identity(it) == id } ?: return
        TransferService.cancelForPc(getApplication(), id)
        AndroidOfferMonitor.get(getApplication()).forget(id)
        SyncStatusStore.get(getApplication()).forget(id)
        store.forget(id)
        val fingerprint = java.security.MessageDigest.getInstance("SHA-256")
            .digest("${forgotten.pcId}:${forgotten.key}".toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
        val cursors = getApplication<android.app.Application>().getSharedPreferences("receive_cursor", android.content.Context.MODE_PRIVATE)
        val editor = cursors.edit().remove("cursor:$fingerprint")
        if (cursors.getString("pc", "") == fingerprint) editor.remove("pc").remove("cursor")
        editor.apply()
        reloadPcs()
        if (paired) reconnect() else show(text(R.string.no_pc_linked))
    }
    fun fileOffersEnabled(enabled: Boolean) {
        store.fileOffers = enabled
        fileOffers = enabled
        if (!enabled) AndroidOfferMonitor.get(getApplication()).clearPending()
    }
}

class MainActivity : ComponentActivity() {
    private var selectedTab by mutableStateOf(0)
    private var fileNotificationsAllowed by mutableStateOf(false)
    private var showFileNotificationSettings by mutableStateOf(false)
    private val model: SettingsViewModel by viewModels()
    private val updates: AppUpdateViewModel by viewModels()
    private val shizuku: ShizukuSettingsModel by viewModels()
    private val installPermission = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (packageManager.canRequestPackageInstalls()) installUpdate()
        else updates.error(getString(R.string.updates_allow_install))
    }
    private val permission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) enableNotification()
        else {
            model.notificationEnabled(false)
            model.show(getString(R.string.notifications_off))
        }
    }
    private val filePermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        fileNotificationsAllowed = AndroidOfferMonitor.notificationsAllowed(this)
        if (fileNotificationsAllowed) refreshAutomaticReceive() else showFileNotificationSettings = true
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        UpdateCheckJob.schedule(this)
        selectedTab = StartTab.initial(intent.getStringExtra(StartTab.EXTRA),
            savedInstanceState?.takeIf { it.containsKey("tab") }?.getInt("tab"), model.paired)
        setContent {
            PuenteTheme {
                LaunchedEffect(model.paired) {
                    if (model.notification) QuickNotification.show(this@MainActivity)
                }
                LaunchedEffect(shizuku.selected, model.paired, model.activePcId, model.fileOffers) {
                    refreshAutomaticReceive()
                }
                LaunchedEffect(model.paired, model.connected) {
                    if (model.paired && model.connected) {
                        updates.autoCheck()
                        refreshAutomaticReceive()
                    }
                }
                Dashboard(model, updates, shizuku, selectedTab, { selectedTab = it },
                    ::openAction, ::toggleNotification, ::refreshAutomaticReceive, ::installUpdate,
                    fileNotificationsAllowed, ::requestFileNotifications)
                if (showFileNotificationSettings) androidx.compose.material3.AlertDialog(
                    onDismissRequest = { showFileNotificationSettings = false },
                    title = { Text(getString(R.string.notification_permission_needed)) },
                    text = { Text(getString(R.string.notification_permission_message)) },
                    confirmButton = { androidx.compose.material3.TextButton(onClick = {
                        showFileNotificationSettings = false
                        startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
                    }) { Text(getString(R.string.open_notification_settings)) } },
                    dismissButton = { androidx.compose.material3.TextButton(onClick = { showFileNotificationSettings = false }) { Text(getString(R.string.close)) } })
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        StartTab.requested(intent.getStringExtra(StartTab.EXTRA))?.let { selectedTab = it }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putInt("tab", selectedTab)
        super.onSaveInstanceState(outState)
    }

    override fun onResume() {
        super.onResume()
        model.reloadAutomaticReceive()
        model.reloadPcs()
        shizuku.refresh()
        fileNotificationsAllowed = AndroidOfferMonitor.notificationsAllowed(this)
        if (model.paired) refreshAutomaticReceive()
        if (model.notification && !QuickNotification.show(this)) model.notificationEnabled(false)
    }

    private fun refreshAutomaticReceive() {
        try { AutoReceiveService.refresh(this) }
        catch (_: Exception) { model.show(getString(R.string.auto_receive_start_failed)) }
    }

    private fun openAction(action: String) {
        startActivity(Intent(this, QuickActionActivity::class.java).setAction(action))
    }

    private fun installUpdate() {
        try {
            if (!packageManager.canRequestPackageInstalls()) {
                updates.error(getString(R.string.updates_authorize))
                installPermission.launch(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:$packageName")))
                return
            }
            updates.install { file ->
                val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
                startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
            }
        } catch (_: Exception) {
            updates.error(getString(R.string.installer_failed))
        }
    }

    private fun toggleNotification(enabled: Boolean) {
        if (!enabled) {
            model.notificationEnabled(false)
            QuickNotification.hide(this)
            return
        }
        if (Build.VERSION.SDK_INT >= 33 && !QuickNotification.allowed(this)) {
            permission.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else enableNotification()
    }

    private fun requestFileNotifications() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            filePermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            fileNotificationsAllowed = AndroidOfferMonitor.notificationsAllowed(this)
            if (fileNotificationsAllowed) refreshAutomaticReceive() else showFileNotificationSettings = true
        }
    }

    private fun enableNotification() {
        val enabled = QuickNotification.show(this)
        model.notificationEnabled(enabled)
        if (!enabled) model.show(getString(R.string.notifications_enable_settings))
    }
}
