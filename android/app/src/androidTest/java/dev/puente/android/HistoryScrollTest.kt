package dev.puente.android

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Rule
import org.junit.Test

class HistoryScrollTest {
    @get:Rule val compose = createComposeRule()

    @Test fun showsSeveralCopiesAndClearsWithoutOpeningMenus() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        var cleared = 0
        var copied = ""
        val entries = (1..4).map {
            HistoryEntry("$it", it.toLong(), HistoryKind.TEXT, "Copy $it", "fingerprint-$it")
        }
        compose.setContent {
            PuenteTheme {
                HistoryFeed(entries, onCopy = { copied = it.id }, onPin = {}, onDelete = {},
                    onOpen = {}, onClear = { cleared++ }, modifier = Modifier.height(480.dp))
            }
        }
        compose.onNodeWithText("Copy 4").assertIsDisplayed()
        compose.onAllNodesWithContentDescription(context.getString(R.string.copy))[0].performClick()
        compose.runOnIdle { org.junit.Assert.assertEquals("1", copied) }
        compose.onNodeWithContentDescription(context.getString(R.string.clear_recent_confirm)).performClick()
        compose.runOnIdle { org.junit.Assert.assertEquals(1, cleared) }
    }

    @Test fun headerAndHistoryShareOneScrollableListOnSmallScreens() {
        val entries = (1..30).map {
            HistoryEntry("$it", it.toLong(), HistoryKind.TEXT, "Copy $it", "fingerprint-$it")
        }
        compose.setContent {
            PuenteTheme {
                HistoryFeed(entries, onCopy = {}, onPin = {}, onDelete = {}, onOpen = {}, onClear = {},
                    modifier = Modifier.height(320.dp), header = {
                        Column(Modifier.height(220.dp)) { Text("Test header") }
                    })
            }
        }
        compose.onNodeWithText("Test header").assertIsDisplayed()
        compose.onNodeWithTag("history-feed").performScrollToNode(hasText("Copy 20"))
        compose.onNodeWithText("Copy 20").assertIsDisplayed()
        compose.onNodeWithText("Test header").assertDoesNotExist()
        compose.onNodeWithTag("history-next").assertIsDisplayed().performClick()
        compose.onNodeWithTag("history-feed").performScrollToNode(hasText("Copy 30"))
        compose.onNodeWithText("Copy 30").assertIsDisplayed()
        compose.onNodeWithText("Test header").assertDoesNotExist()
        compose.onNodeWithTag("history-previous").assertIsDisplayed().performClick()
        compose.onNodeWithTag("history-feed").performScrollToNode(hasText("Copy 1"))
        compose.onNodeWithText("Copy 1").assertIsDisplayed()
    }

    @Test fun pairingProblemRemainsVisibleAndOpensTheReason() {
        compose.setContent { PuenteTheme { ConnectionProblem("The request expired. Try again.") } }
        compose.onNodeWithText(InstrumentationRegistry.getInstrumentation().targetContext.getString(R.string.check_link)).assertIsDisplayed().performClick()
        compose.onNodeWithText("The request expired. Try again.").assertIsDisplayed()
    }
}
