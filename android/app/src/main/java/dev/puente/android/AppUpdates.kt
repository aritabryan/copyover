package dev.puente.android

import android.app.Application
import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.UUID

data class AppUpdate(val versionCode: Long, val versionName: String, val size: Long, val sha256: String,
    val downloadUrl: String = "") {
    companion object {
        fun parse(json: JSONObject): AppUpdate {
            val update = AppUpdate(json.getLong("versionCode"), json.getString("versionName"),
                json.getLong("size"), json.getString("sha256"))
            if (update.versionCode !in 1..2100000000 || update.versionName.length !in 1..80 ||
                update.size !in 1..UpdatePolicy.MAX_BYTES || !Regex("[a-f0-9]{64}").matches(update.sha256)) {
                throw LocalizedException(R.string.update_invalid)
            }
            return update
        }
    }
}

@Suppress("DEPRECATION")
private fun PackageInfo.code(): Long = if (Build.VERSION.SDK_INT >= 28) longVersionCode else versionCode.toLong()

@Suppress("DEPRECATION")
private fun PackageInfo.signers(): Set<String> {
    val certificates = if (Build.VERSION.SDK_INT >= 28) signingInfo?.apkContentsSigners else signatures
    return certificates.orEmpty().map { digest(it.toByteArray()) }.toSet()
}

private fun digest(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
    .digest(bytes).joinToString("") { "%02x".format(it) }

class AppUpdateRepository(private val context: Context) {
    private val github = GitHubUpdates()
    @Suppress("DEPRECATION")
    private val installed get() = context.packageManager.getPackageInfo(context.packageName, signatureFlags())
    val installedCode get() = installed.code()
    val installedName get() = installed.versionName.orEmpty()

    private fun signatureFlags(): Int = if (Build.VERSION.SDK_INT >= 28)
        PackageManager.GET_SIGNING_CERTIFICATES else @Suppress("DEPRECATION") PackageManager.GET_SIGNATURES

    fun check(): Pair<AppUpdate?, String> {
        val update = github.check(installedCode)
        return update to context.getString(R.string.update_latest)
    }

    fun download(update: AppUpdate): File {
        val root = File(context.cacheDir, "updates").apply { mkdirs() }
        // Each transfer is independent; an interrupted download cannot become an installer candidate.
        root.listFiles()?.filter { it.lastModified() < System.currentTimeMillis() - 86400000L }
            ?.forEach { it.deleteRecursively() }
        val directory = File(root, UUID.randomUUID().toString()).apply { check(mkdir()) }
        return try {
            val target = File(directory, "Puente.apk")
            github.download(update, target)
            validate(target, update)
            target
        } catch (error: Exception) {
            directory.deleteRecursively()
            throw error
        }
    }

    @Suppress("DEPRECATION")
    fun validate(file: File, update: AppUpdate) {
        val hash = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(65536)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                hash.update(buffer, 0, count)
            }
        }
        val actual = hash.digest().joinToString("") { "%02x".format(it) }
        require(UpdatePolicy.matches(update.size, update.sha256, file.length(), actual)) {
            context.getString(R.string.update_incomplete)
        }
        val candidate = context.packageManager.getPackageArchiveInfo(file.absolutePath, signatureFlags())
            ?: throw IOException(context.getString(R.string.update_not_android_app))
        val current = installed
        require(UpdatePolicy.accepts(candidate.packageName, current.packageName, candidate.code(), current.code(),
            update.versionCode, candidate.signers(), current.signers())) {
            context.getString(R.string.update_not_signed)
        }
    }

}

class AppUpdateViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = AppUpdateRepository(application)
    private fun text(id: Int, vararg args: Any): String = getApplication<Application>().getString(id, *args)
    private val preferences = application.getSharedPreferences("updates", Context.MODE_PRIVATE)
    val installedVersion = repository.installedName
    var automatic by mutableStateOf(preferences.getBoolean("automatic", true))
        private set
    var busy by mutableStateOf(false)
        private set
    var message by mutableStateOf(text(R.string.updates_from_github))
        private set
    var available by mutableStateOf<AppUpdate?>(null)
        private set
    var downloaded by mutableStateOf<File?>(null)
        private set
    private var checkedAutomatically = false

    fun autoCheck() {
        if (automatic && !checkedAutomatically && !busy) {
            checkedAutomatically = true
            check()
        }
    }

    fun automaticEnabled(enabled: Boolean) {
        automatic = enabled
        preferences.edit().putBoolean("automatic", enabled).apply()
    }

    fun check() = operation(text(R.string.update_checking)) {
        val (update, status) = withContext(Dispatchers.IO) { repository.check() }
        if (update != available) downloaded = null
        available = update
        // The app is open and shows it: a notification for this version would only repeat it.
        UpdateNotification.seen(getApplication(), update)
        message = if (update != null) text(R.string.update_available, update.versionName) else status
    }

    fun download() {
        val update = available ?: return
        operation(text(R.string.update_downloading)) {
            downloaded = withContext(Dispatchers.IO) { repository.download(update) }
            message = text(R.string.update_ready)
        }
    }

    fun install(ready: (File) -> Unit) {
        val file = downloaded ?: return
        val update = available ?: return
        operation(text(R.string.update_verifying)) {
            try {
                withContext(Dispatchers.IO) { repository.validate(file, update) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                downloaded = null
                throw error
            }
            message = text(R.string.update_confirm)
            ready(file)
        }
    }

    fun error(text: String) { message = text }

    private fun operation(status: String, block: suspend () -> Unit) {
        if (busy) return
        busy = true
        message = status
        viewModelScope.launch {
            try { block() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { message = getApplication<Application>().describe(error, R.string.update_failed) }
            catch (_: UnsatisfiedLinkError) { message = text(R.string.reinstall_for_updates) }
            finally { busy = false }
        }
    }
}
