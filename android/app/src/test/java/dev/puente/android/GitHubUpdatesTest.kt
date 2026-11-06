package dev.puente.android

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

class GitHubUpdatesTest {
    private val prefix = "https://github.com/aritabryan/copyover/releases/download/v1.0.2/"
    private val hash = "a".repeat(64)
    private val requests = mutableListOf<String>()
    private val responses = mutableMapOf<String, Response>()

    private data class Response(val bytes: ByteArray, val status: Int = 200, val location: String? = null)

    private fun client() = GitHubUpdates { url ->
        requests += url.toString()
        val response = responses[url.toString()] ?: if (url.path.endsWith("/releases")) Response("[]".toByteArray())
            else error("Unexpected request: $url")
        object : HttpURLConnection(url) {
            override fun connect() {}
            override fun disconnect() {}
            override fun usingProxy() = false
            override fun getResponseCode() = response.status
            override fun getInputStream() = ByteArrayInputStream(response.bytes)
            override fun getHeaderField(name: String) = if (name == "Location") response.location else null
        }
    }

    private fun asset(name: String, size: Long) = JSONObject().put("name", name).put("size", size)
        .put("browser_download_url", prefix + name)

    private fun release(code: Long = 24): JSONObject {
        val manifest = JSONObject().put("versionCode", code).put("versionName", "1.0.1")
            .put("size", 3).put("sha256", hash).toString().toByteArray()
        responses[prefix + "puente-update.json"] = Response(manifest)
        return JSONObject().put("tag_name", "v1.0.2").put("draft", false).put("prerelease", false)
            .put("assets", JSONArray().put(asset("Puente.apk", 3)).put(asset("puente-update.json", manifest.size.toLong())))
    }

    private fun offer(value: JSONObject) {
        responses[GitHubUpdates.LATEST_RELEASE] = Response(value.toString().toByteArray())
    }

    @Test fun checksAndroidManifestWithoutAnyPcAndPinsDownloadToRelease() {
        offer(release())
        val update = client().check(23)!!
        assertEquals(24L, update.versionCode)
        assertEquals("1.0.1", update.versionName)
        assertEquals(prefix + "Puente.apk", update.downloadUrl)
        assertEquals(listOf(GitHubUpdates.LATEST_RELEASE, prefix + "puente-update.json"), requests)
    }

    @Test fun windowsReleaseWithSameApkDoesNotOfferAnUpdate() {
        offer(release(code = 23))
        assertNull(client().check(23))
    }

    @Test fun releaseWithoutAndroidAssetsIsNotAnAndroidUpdate() {
        offer(release().put("assets", JSONArray().put(asset("CopyOver-Setup-1.0.2.exe", 3))))
        assertNull(client().check(23))
    }

    @Test fun windowsOnlyReleaseDoesNotHideAnOlderAndroidRelease() {
        val android = release()
        offer(release().put("assets", JSONArray().put(asset("CopyOver-Setup-1.0.2.exe", 3))))
        responses["https://api.github.com/repos/aritabryan/copyover/releases?per_page=20"] =
            Response(JSONArray().put(android).toString().toByteArray())
        assertEquals(24L, client().check(23)!!.versionCode)
    }

    @Test fun ignoresDraftsAndPrereleases() {
        for (flag in listOf("draft", "prerelease")) {
            offer(release().put(flag, true))
            assertNull(client().check(23))
        }
    }

    @Test fun refusesManifestAndAssetSizeMismatch() {
        offer(release().put("assets", JSONArray().put(asset("Puente.apk", 4)).put(asset("puente-update.json", 180))))
        assertThrows(Exception::class.java) { client().check(23) }
    }

    @Test fun refusesAssetsOutsideThisRepository() {
        val value = release()
        value.getJSONArray("assets").getJSONObject(0).put("browser_download_url", "https://example.invalid/Puente.apk")
        offer(value)
        assertThrows(Exception::class.java) { client().check(23) }
        assertEquals(listOf(GitHubUpdates.LATEST_RELEASE), requests)
    }

    @Test fun apiFailureDoesNotPretendThereAreNoUpdates() {
        responses[GitHubUpdates.LATEST_RELEASE] = Response(ByteArray(0), status = 403)
        assertThrows(Exception::class.java) { client().check(23) }
    }

    @Test fun refusesHashMismatchAndInvalidManifest() {
        offer(release().also { it.getJSONArray("assets").getJSONObject(0).put("digest", "sha256:" + "b".repeat(64)) })
        assertThrows(Exception::class.java) { client().check(23) }
        offer(release())
        responses[prefix + "puente-update.json"] = Response("{}".toByteArray())
        assertThrows(Exception::class.java) { client().check(23) }
    }

    @Test fun oversizedManifestIsRejected() {
        offer(release())
        responses[prefix + "puente-update.json"] = Response(ByteArray(4097) { 32 })
        assertThrows(Exception::class.java) { client().check(23) }
    }

    @Test fun redirectsCannotEscapeGithubOrLoopForever() {
        for (destination in listOf("https://example.invalid/apk", prefix + "Puente.apk")) {
            requests.clear()
            responses[prefix + "Puente.apk"] = Response(ByteArray(0), status = 302, location = destination)
            val target = File.createTempFile("update", ".apk")
            try {
                assertThrows(Exception::class.java) {
                    client().download(AppUpdate(24, "1.0.1", 3, hash, prefix + "Puente.apk"), target)
                }
                assertFalse(target.exists())
                assertTrue(requests.size <= 6)
            } finally { target.delete() }
        }
    }

    @Test fun publicReleaseSmokeTest() {
        assumeTrue(System.getenv("COPYOVER_LIVE_UPDATE_TEST") == "1")
        val update = GitHubUpdates().check(0)!!
        assertTrue(update.versionCode > 0)
        assertTrue(update.downloadUrl.startsWith("https://github.com/aritabryan/copyover/releases/download/"))
    }

    @Test fun followsHttpsGithubDownloadRedirects() {
        val cdn = "https://release-assets.githubusercontent.com/asset"
        responses[prefix + "Puente.apk"] = Response(ByteArray(0), status = 302, location = cdn)
        responses[cdn] = Response("apk".toByteArray())
        val target = File.createTempFile("update", ".apk")
        try {
            client().download(AppUpdate(24, "1.0.1", 3, hash, prefix + "Puente.apk"), target)
            assertEquals("apk", target.readText())
        } finally { target.delete() }
    }

    @Test fun refusesInsecureRedirectAndDeletesPartialDownload() {
        responses[prefix + "Puente.apk"] = Response(ByteArray(0), status = 302, location = "http://github.com/asset")
        val target = File.createTempFile("update", ".apk")
        try {
            assertThrows(Exception::class.java) {
                client().download(AppUpdate(24, "1.0.1", 3, hash, prefix + "Puente.apk"), target)
            }
            assertFalse(target.exists())
        } finally { target.delete() }
    }

    @Test fun oversizedAndTruncatedDownloadsAreDeleted() {
        for (body in listOf("ap", "apk-too-long")) {
            responses[prefix + "Puente.apk"] = Response(body.toByteArray())
            val target = File.createTempFile("update", ".apk")
            try {
                assertThrows(Exception::class.java) {
                    client().download(AppUpdate(24, "1.0.1", 3, hash, prefix + "Puente.apk"), target)
                }
                assertFalse(target.exists())
            } finally { target.delete() }
        }
    }
}
