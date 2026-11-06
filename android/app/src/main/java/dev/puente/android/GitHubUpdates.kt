package dev.puente.android

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL

class GitHubUpdates(private val open: (URL) -> HttpURLConnection = { it.openConnection() as HttpURLConnection }) {
    companion object {
        const val LATEST_RELEASE = "https://api.github.com/repos/aritabryan/copyover/releases/latest"
        private const val DOWNLOADS = "https://github.com/aritabryan/copyover/releases/download/"
        private val HOSTS = setOf("api.github.com", "github.com", "release-assets.githubusercontent.com",
            "objects.githubusercontent.com")
    }

    fun check(installedCode: Long): AppUpdate? {
        val release = json(LATEST_RELEASE, 1024 * 1024)
        val latest = updateFrom(release)
        if (latest != null) return latest.takeIf { it.versionCode > installedCode }
        val recent = JSONArray(text("https://api.github.com/repos/aritabryan/copyover/releases?per_page=20", 1024 * 1024))
        for (index in 0 until recent.length()) {
            val update = updateFrom(recent.getJSONObject(index)) ?: continue
            return update.takeIf { it.versionCode > installedCode }
        }
        return null
    }

    private fun updateFrom(release: JSONObject): AppUpdate? {
        if (release.optBoolean("draft") || release.optBoolean("prerelease")) return null
        val assets = release.getJSONArray("assets")
        val files = (0 until assets.length()).map { assets.getJSONObject(it) }.associateBy { it.getString("name") }
        val apk = files["Puente.apk"] ?: return null
        val manifest = files["puente-update.json"] ?: return null
        val prefix = DOWNLOADS + release.getString("tag_name") + "/"
        val apkUrl = assetUrl(apk, prefix, "Puente.apk", UpdatePolicy.MAX_BYTES)
        val manifestUrl = assetUrl(manifest, prefix, "puente-update.json", 4096)
        val update = AppUpdate.parse(json(manifestUrl, 4096)).copy(downloadUrl = apkUrl)
        require(update.size == apk.getLong("size")) { "The APK size does not match its manifest." }
        val publishedHash = apk.optString("digest")
        require(publishedHash.isEmpty() || publishedHash == "sha256:${update.sha256}") {
            "The APK hash does not match its manifest."
        }
        return update
    }

    fun download(update: AppUpdate, target: File) {
        try {
            require(update.size in 1..UpdatePolicy.MAX_BYTES)
            require(update.downloadUrl.startsWith(DOWNLOADS))
            val count = target.outputStream().use { output -> read(update.downloadUrl, update.size, output) }
            if (count != update.size) throw IOException("The APK download is incomplete.")
        } catch (error: Exception) {
            target.delete()
            throw error
        }
    }

    private fun assetUrl(asset: JSONObject, prefix: String, name: String, limit: Long): String {
        require(asset.getLong("size") in 1..limit) { "Invalid update asset size." }
        return asset.getString("browser_download_url").also {
            require(it == prefix + name) { "The update asset is outside the release." }
        }
    }

    private fun json(address: String, limit: Long): JSONObject = JSONObject(text(address, limit))

    private fun text(address: String, limit: Long): String = ByteArrayOutputStream().use { output ->
        read(address, limit, output)
        output.toString("UTF-8")
    }

    private fun read(address: String, limit: Long, output: OutputStream): Long {
        var url = URL(address)
        repeat(6) {
            require(url.protocol == "https" && url.host in HOSTS && url.userInfo == null &&
                url.port in setOf(-1, 443)) { "Untrusted update URL." }
            val connection = open(url).apply {
                connectTimeout = 15000
                readTimeout = 30000
                instanceFollowRedirects = false
                setRequestProperty("User-Agent", "CopyOver-Android")
                setRequestProperty("Accept", if (url.host == "api.github.com") "application/vnd.github+json"
                    else "application/octet-stream")
            }
            try {
                val status = connection.responseCode
                if (status in setOf(301, 302, 303, 307, 308)) {
                    val location = connection.getHeaderField("Location") ?: throw IOException("Missing download redirect.")
                    url = URL(url, location)
                } else {
                    if (status != 200) throw LocalizedException(R.string.update_github_unavailable)
                    return connection.inputStream.use { input -> copyLimited(input, output, limit) }
                }
            } finally { connection.disconnect() }
        }
        throw IOException("Too many update redirects.")
    }

    private fun copyLimited(input: InputStream, output: OutputStream, limit: Long): Long {
        var total = 0L
        val buffer = ByteArray(65536)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) return total
            total += count
            if (total > limit) throw IOException("The update download exceeds its expected size.")
            output.write(buffer, 0, count)
        }
    }
}
