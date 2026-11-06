package dev.puente.android

import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class SettingsControlsTest {
    @get:Rule val compose = createComposeRule()
    private fun text(id: Int) = InstrumentationRegistry.getInstrumentation().targetContext.getString(id)

    @Test fun labelTogglesOnceAndHelpDoesNotToggle() {
        val checked = mutableStateOf(false)
        var changes = 0
        var help = 0
        val label = "Receive from PC"
        val description = InstrumentationRegistry.getInstrumentation().targetContext.getString(R.string.setting_help_label, label)
        compose.setContent {
            PuenteTheme {
                SettingControl(label, R.drawable.ic_down, checked.value, onInfo = { help++ }) {
                    changes++; checked.value = it
                }
            }
        }
        compose.onNodeWithText(label).performClick()
        compose.runOnIdle { assertEquals(1, changes); assertEquals(true, checked.value) }
        compose.onNodeWithContentDescription(description).performClick()
        compose.runOnIdle { assertEquals(1, help); assertEquals(1, changes) }
    }

    @Test fun successfulConnectionShowsPcAndCanBeDismissed() {
        val feedback = mutableStateOf(ConnectionCheckFeedback(ConnectionCheckPhase.SUCCESS, pcName = "Office-PC"))
        compose.setContent {
            PuenteTheme { ConnectionCheckResult(feedback.value, { feedback.value = ConnectionCheckFeedback(ConnectionCheckPhase.IDLE) }, {}) }
        }
        compose.onNodeWithText("Office-PC").assertIsDisplayed()
        compose.onNodeWithText(text(R.string.connection_check_success)).assertIsDisplayed()
        compose.onNodeWithText(text(R.string.done)).performClick()
        compose.onNodeWithText("Office-PC").assertDoesNotExist()
    }

    @Test fun failedConnectionShowsReasonAndRetries() {
        var retries = 0
        val feedback = mutableStateOf(ConnectionCheckFeedback(ConnectionCheckPhase.ERROR, message = "PC offline"))
        compose.setContent {
            PuenteTheme { ConnectionCheckResult(feedback.value, { feedback.value = ConnectionCheckFeedback(ConnectionCheckPhase.IDLE) }, { retries++ }) }
        }
        compose.onNodeWithText("PC offline").assertIsDisplayed()
        compose.onNodeWithText(text(R.string.retry)).performClick()
        compose.runOnIdle { assertEquals(1, retries) }
        compose.onNodeWithText("PC offline").assertDoesNotExist()
    }
}
