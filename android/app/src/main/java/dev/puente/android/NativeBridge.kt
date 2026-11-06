package dev.puente.android

internal object NativeBridge {
    init { System.loadLibrary("puente_core") }
    external fun execute(request: String): String
    external fun executeTracked(id: String, request: String): String
    external fun progress(id: String): String
    external fun cancel(id: String)
}
