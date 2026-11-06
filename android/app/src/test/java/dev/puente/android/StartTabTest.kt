package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class StartTabTest {
    @Test fun updateNoticeOpensSettings() {
        assertEquals(StartTab.SETTINGS, StartTab.requested("settings"))
    }

    @Test fun firstLaunchWithoutPcOpensDevices() {
        assertEquals(StartTab.DEVICES, StartTab.initial(null, null, paired = false))
        assertEquals(StartTab.CLIPBOARD, StartTab.initial(null, null, paired = true))
    }
    @Test fun explicitRequestWinsOverPairingAndRestoredTab() {
        assertEquals(StartTab.FILES, StartTab.initial("files", StartTab.CLIPBOARD, paired = false))
        assertEquals(StartTab.DEVICES, StartTab.initial("devices", StartTab.FILES, paired = true))
    }
    @Test fun rotationKeepsTheChosenTab() {
        assertEquals(StartTab.CLIPBOARD, StartTab.initial(null, StartTab.CLIPBOARD, paired = false))
        assertEquals(3, StartTab.initial("otra", 3, paired = true))
    }
}
