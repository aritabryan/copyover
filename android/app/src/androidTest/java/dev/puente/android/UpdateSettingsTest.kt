package dev.puente.android

import android.app.Application
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Rule
import org.junit.Test

class UpdateSettingsTest {
    @get:Rule val compose = createComposeRule()

    @Test fun canCheckGithubWithoutAPairedPc() {
        val application = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        val updates = AppUpdateViewModel(application)
        compose.setContent { PuenteTheme { UpdateSettings(updates, install = {}) } }
        compose.onNodeWithText(application.getString(R.string.check_updates)).assertIsEnabled()
        compose.onNodeWithText(application.getString(R.string.updates_from_github)).assertIsDisplayed()
    }
}
