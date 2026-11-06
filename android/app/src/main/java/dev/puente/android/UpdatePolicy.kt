package dev.puente.android

object UpdatePolicy {
    /** Each version is announced once, and never one the phone already has. */
    fun shouldNotify(versionCode: Long, installedCode: Long, lastAnnounced: Long): Boolean =
        versionCode > installedCode && versionCode > lastAnnounced

    const val MAX_BYTES = 200L * 1024 * 1024

    fun matches(expectedSize: Long, expectedHash: String, actualSize: Long, actualHash: String): Boolean =
        expectedSize in 1..MAX_BYTES && expectedSize == actualSize &&
            Regex("[a-f0-9]{64}").matches(expectedHash) && expectedHash == actualHash

    fun accepts(candidatePackage: String, installedPackage: String, candidateVersion: Long,
        installedVersion: Long, expectedVersion: Long, candidateSigners: Set<String>, installedSigners: Set<String>): Boolean =
        candidatePackage == installedPackage && candidateVersion > installedVersion &&
            candidateVersion == expectedVersion && installedSigners.isNotEmpty() && candidateSigners == installedSigners
}
