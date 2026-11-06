package dev.puente.android

import android.content.Intent
import android.widget.FrameLayout
import android.widget.RemoteViews
import androidx.lifecycle.Lifecycle
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class QuickPanelTest {
    private val context = ApplicationProvider.getApplicationContext<android.content.Context>()

    private fun waitFor(condition: () -> Boolean): Boolean {
        repeat(100) { if (condition()) return true; Thread.sleep(100) }
        return condition()
    }

    private fun scenarioState(scenario: ActivityScenario<QuickActionActivity>) = scenario.state

    @Test fun successClosesThePanelAndFailureKeepsItOpen() {
        val intent = Intent(context, QuickActionActivity::class.java).setAction(QuickNotification.RECEIVE)
        val scenario = ActivityScenario.launch<QuickActionActivity>(intent)
        // Without a paired PC the receive fails: the reason stays on screen.
        assertTrue(waitFor { TransferRecords.state.value.failed })
        Thread.sleep(QuickPanelPolicy.CLOSE_DELAY_MS + 800)
        assertEquals(Lifecycle.State.RESUMED, scenarioState(scenario))
        TransferRecords.update(context, TransferState(finished = true, message = "ok", result = TransferResult("ok")))
        assertTrue(waitFor { scenarioState(scenario) == Lifecycle.State.DESTROYED })
    }

    @Test fun widgetLayoutInflatesAsRemoteViews() {
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            val view = RemoteViews(context.packageName, R.layout.widget_actions).apply(context, FrameLayout(context))
            listOf(R.id.send, R.id.receive, R.id.attach).forEach { assertNotNull(view.findViewById(it)) }
        }
    }
}
