package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class FileSpacePolicyTest {
    @Test fun fileSizeDependsOnAvailableDiskRatherThanFixedCap() {
        val twentyGiB = 20L * 1024 * 1024 * 1024
        assertTrue(FileSpacePolicy.fits(twentyGiB, twentyGiB + FileSpacePolicy.RESERVE_BYTES))
        assertFalse(FileSpacePolicy.fits(twentyGiB, twentyGiB))
        assertTrue(FileSpacePolicy.fits(0, FileSpacePolicy.RESERVE_BYTES))
    }

    @Test fun invalidAndOverflowingSizesCannotPassTheDiskCheck() {
        assertFalse(FileSpacePolicy.fits(-1, Long.MAX_VALUE))
        assertFalse(FileSpacePolicy.fits(Long.MAX_VALUE, Long.MAX_VALUE))
        assertFalse(FileSpacePolicy.fits(1, -1))
        assertFalse(FileSpacePolicy.fits(1, FileSpacePolicy.RESERVE_BYTES))
    }
}
