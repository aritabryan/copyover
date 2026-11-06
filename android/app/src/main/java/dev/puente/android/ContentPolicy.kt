package dev.puente.android

object ContentPolicy {
    const val MAX_TEXT_BYTES = 4 * 1024 * 1024

    fun safeName(name: String): String {
        val cleaned = name.map { character ->
            if (character.isISOControl() || character in "/\\:*?\"<>|") '_' else character
        }.joinToString("").trim(' ', '.').ifEmpty { "file.bin" }
        if (cleaned.toByteArray(Charsets.UTF_8).size <= 200) return cleaned
        val suffix = cleaned.substringAfterLast('.', "")
        val extension = if (suffix.isNotEmpty() && suffix.toByteArray(Charsets.UTF_8).size <= 32) ".$suffix" else ""
        return truncateUtf8(cleaned.removeSuffix(extension), 200 - extension.toByteArray(Charsets.UTF_8).size) + extension
    }

    private fun truncateUtf8(value: String, maxBytes: Int): String {
        val result = StringBuilder()
        var bytes = 0
        for (codePoint in value.codePoints().toArray()) {
            val character = String(Character.toChars(codePoint))
            bytes += character.toByteArray(Charsets.UTF_8).size
            if (bytes > maxBytes) break
            result.append(character)
        }
        return result.toString()
    }

    fun validateText(text: String): String {
        if (text.toByteArray(Charsets.UTF_8).size > MAX_TEXT_BYTES) throw LocalizedException(R.string.text_too_long)
        return text
    }

}

/** Retained in ViewModel: rotation or repeated focus events must not resend a clipboard. */
class FocusGate {
    private var claimed = false
    val wasClaimed: Boolean get() = claimed
    fun restoreClaimed(saved: Boolean) { claimed = claimed || saved }
    fun claim(hasFocus: Boolean): Boolean {
        if (!hasFocus || claimed) return false
        claimed = true
        return true
    }
}
