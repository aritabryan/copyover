package dev.puente.android;

import android.os.Bundle;
import android.os.ParcelFileDescriptor;

interface IClipboardAccess {
    void destroy() = 16777114;
    Bundle snapshot() = 1;
    ParcelFileDescriptor openImage(String expectedToken) = 2;
}
