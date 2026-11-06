package dev.puente.android

import android.Manifest
import android.content.pm.PackageManager
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Test

class StartupTest {
    @Test fun launchesAndSchedulesNetworkChecksWithoutAPairedPc() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assertEquals(PackageManager.PERMISSION_GRANTED, context.checkSelfPermission(Manifest.permission.ACCESS_NETWORK_STATE))
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { UpdateCheckJob.schedule(it) }
        }
    }
}
