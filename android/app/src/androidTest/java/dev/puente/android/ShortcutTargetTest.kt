package dev.puente.android

import android.content.Intent
import android.util.Xml
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.xmlpull.v1.XmlPullParser

@RunWith(AndroidJUnit4::class)
class ShortcutTargetTest {
    @Test fun everyShortcutTargetsTheInstalledPackage() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        var count = 0
        context.resources.getXml(R.xml.shortcuts).use { parser ->
            while (parser.next() != XmlPullParser.END_DOCUMENT) {
                if (parser.eventType == XmlPullParser.START_TAG && parser.name == "intent") {
                    val intent = Intent.parseIntent(context.resources, parser, Xml.asAttributeSet(parser))
                    assertEquals(context.packageName, intent.component?.packageName)
                    assertEquals(QuickActionActivity::class.java.name, intent.component?.className)
                    count++
                }
            }
        }
        assertEquals(3, count)
    }
}
