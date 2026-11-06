package dev.puente.android

import android.content.ClipData
import android.content.Context
import android.os.Binder
import android.os.Bundle
import android.os.IBinder
import android.os.ParcelFileDescriptor
import androidx.annotation.Keep
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlin.system.exitProcess

/** Runs as shell through Shizuku. Never receives a PC key or performs networking. */
@Keep
class ShizukuClipboardService @Keep constructor(context: Context) : IClipboardAccess.Stub() {
    private val owner = context.applicationInfo.uid
    private val user = owner / 100000
    @Suppress("DEPRECATION")
    private fun useShellIdentity() { if (android.os.Process.myUid() == 0) android.system.Os.setuid(2000) }
    init { useShellIdentity() }
    private val exporting = AtomicBoolean(false)
    private val timer = Executors.newSingleThreadScheduledExecutor()
    private val activeProcess = AtomicReference<Process?>()
    private val clipboard by lazy {
        val binder = Class.forName("android.os.ServiceManager").getMethod("getService", String::class.java)
            .invoke(null, "clipboard") as IBinder
        Class.forName("android.content.IClipboard\$Stub").getMethod("asInterface", IBinder::class.java)
            .invoke(null, binder)
    }

    private fun enforceOwner() { check(Binder.getCallingUid() == owner) { "Unauthorized client." } }

    private fun clip(): ClipData? {
        val method = Class.forName("android.content.IClipboard").methods.single { it.name == "getPrimaryClip" }
        val args = ClipboardApiArguments.build(method.parameterTypes.map { it.name }, user)
        return method.invoke(clipboard, *args.toTypedArray()) as? ClipData
    }

    private fun locked(): Boolean {
        val binder = Class.forName("android.os.ServiceManager").getMethod("getService", String::class.java)
            .invoke(null, "window") as IBinder
        val window = Class.forName("android.view.IWindowManager\$Stub").getMethod("asInterface", IBinder::class.java)
            .invoke(null, binder)
        return Class.forName("android.view.IWindowManager").getMethod("isKeyguardLocked").invoke(window) as Boolean
    }

    private fun token(clip: ClipData): String {
        val item = clip.getItemAt(0)
        val bytes = "${clip.description.timestamp}:${item.text}:${item.uri}".toByteArray(Charsets.UTF_8)
        return MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    }

    private inline fun <T> privileged(operation: () -> T): T {
        enforceOwner()
        val identity = Binder.clearCallingIdentity()
        try { return operation() } finally { Binder.restoreCallingIdentity(identity) }
    }

    override fun snapshot(): Bundle = privileged { snapshotInternal() }

    private fun snapshotInternal(): Bundle {
        return try {
            val clip = clip() ?: return Bundle().apply {
                putString("kind", if (locked()) "unavailable" else "empty")
                putString("token", "empty")
            }
            val result = Bundle().apply {
                putString("token", token(clip))
                putBoolean("from_pc", clip.description.extras?.getBoolean(PcClipboard.ORIGIN, false) == true)
                putBoolean("sensitive", HistoryStore.isSensitive(clip.description))
            }
            val item = clip.getItemAt(0)
            val text = item.text?.toString()
            if (!text.isNullOrEmpty() && AutomaticClipboardPolicy.canCopyText(text)) {
                result.putString("kind", "text"); result.putString("text", text)
            } else if (item.uri?.scheme == "content" && clip.description.hasMimeType("image/*")) {
                val mime = (0 until clip.description.mimeTypeCount)
                    .map { clip.description.getMimeType(it) }.first { it.startsWith("image/") }
                result.putString("kind", if (ShizukuModePolicy.imageExtension(mime) != null) "image" else "unsupported")
                result.putString("mime", mime)
            } else result.putString("kind", "unsupported")
            result
        } catch (_: Exception) {
            Bundle().apply { putString("kind", "error"); putString("message", "Couldn't access the clipboard with Shizuku on this device.") }
        }
    }

    override fun openImage(expectedToken: String): ParcelFileDescriptor = privileged { openImageInternal(expectedToken) }

    private fun openImageInternal(expectedToken: String): ParcelFileDescriptor {
        val current = requireNotNull(clip()) { "The clipboard changed." }
        require(token(current) == expectedToken && current.description.extras?.getBoolean(PcClipboard.ORIGIN, false) != true) {
            "The clipboard changed."
        }
        val uri = requireNotNull(current.getItemAt(0).uri)
        require(uri.scheme == "content" && current.description.hasMimeType("image/*"))
        check(exporting.compareAndSet(false, true)) { "An image is already being transferred." }
        val pipe = try { ParcelFileDescriptor.createReliablePipe() } catch (error: Exception) { exporting.set(false); throw error }
        Thread({ exportImage(uri.toString(), pipe[1]) }, "clipboard-image").start()
        return pipe[0]
    }

    private fun exportImage(uri: String, output: ParcelFileDescriptor) {
        val process = AtomicReference<Process?>()
        val deadline = timer.schedule({ runCatching { output.closeWithError("Reading the image took too long.") }; process.get()?.destroy() }, 25, TimeUnit.SECONDS)
        try {
            val reader = ProcessBuilder("/system/bin/content", "read", "--uri", uri, "--user", user.toString())
                .redirectError(File("/dev/null")).start()
            process.set(reader)
            activeProcess.set(reader)
            reader.inputStream.use { input ->
                val target = ParcelFileDescriptor.AutoCloseOutputStream(output)
                    var size = 0L
                    val buffer = ByteArray(65536)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        size += count
                        check(size <= 32L * 1024 * 1024) { "The image exceeds 32 MiB." }
                        target.write(buffer, 0, count)
                    }
                    check(reader.waitFor(2, TimeUnit.SECONDS) && reader.exitValue() == 0 && size > 0) { "Couldn't read the image." }
                    target.close()
            }
        } catch (_: Exception) {
            runCatching { output.closeWithError("Couldn't read the image from the clipboard.") }
        } finally {
            deadline.cancel(false)
            process.get()?.destroy()
            activeProcess.compareAndSet(process.get(), null)
            runCatching { output.close() }
            exporting.set(false)
        }
    }

    override fun destroy() {
        val caller = Binder.getCallingUid()
        check(caller == owner || caller == 0 || caller == 2000) { "Unauthorized client." }
        activeProcess.getAndSet(null)?.destroy()
        timer.shutdownNow()
        exitProcess(0)
    }
}
