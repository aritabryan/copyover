package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class LicenseCatalogTest {
    private val sample = """
        {
          "libraries": [
            {"uniqueId": "dev.rikka.shizuku:api", "name": "api", "artifactVersion": "13.1.5", "licenses": ["MIT"]},
            {"uniqueId": "androidx.core:core", "name": "Core", "artifactVersion": "1.16.0", "licenses": ["Apache-2.0"]},
            {"uniqueId": "example:nameless", "artifactVersion": "", "licenses": ["Custom-1"]}
          ],
          "licenses": {
            "MIT": {"name": "MIT License", "content": "MIT text\n"},
            "Apache-2.0": {"name": "Apache License 2.0", "content": "Apache text"}
          }
        }
    """.trimIndent()

    @Test fun parsesLibrariesSortedByNameWithTheirLicenseTexts() {
        val notices = LicenseCatalog.parse(sample)
        assertEquals(listOf("api", "Core", "example:nameless"), notices.map { it.name })
        val shizuku = notices.first()
        assertEquals("13.1.5", shizuku.version)
        assertEquals(listOf("MIT License"), shizuku.licenseNames)
        assertEquals("MIT text", shizuku.licenseText)
        assertEquals("Apache text", notices[1].licenseText)
    }

    @Test fun unknownLicenseKeepsItsIdAndHasNoText() {
        val unknown = LicenseCatalog.parse(sample).last()
        assertEquals(listOf("Custom-1"), unknown.licenseNames)
        assertEquals("", unknown.licenseText)
    }

    @Test fun missingSectionsGiveAnEmptyList() {
        assertTrue(LicenseCatalog.parse("{}").isEmpty())
    }

    @Test fun paragraphsSplitOnBlankLines() {
        assertEquals(listOf("one\nline", "two"), LicenseCatalog.paragraphs("one\r\nline\r\n\r\n\r\ntwo\n"))
    }

    @Test fun paragraphsMoveCenteredTitlesToTheMargin() {
        val apache = "                                 Apache License\n" +
            "                           Version 2.0, January 2004\n\n" +
            "   TERMS AND CONDITIONS\n\n      \"License\" shall mean"
        assertEquals(listOf("Apache License\nVersion 2.0, January 2004", "   TERMS AND CONDITIONS", "      \"License\" shall mean"),
            LicenseCatalog.paragraphs(apache))
    }
}
