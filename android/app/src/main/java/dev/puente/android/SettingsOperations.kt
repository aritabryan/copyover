package dev.puente.android

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class ConnectionCheckPhase { IDLE, CHECKING, SUCCESS, ERROR }
data class ConnectionCheckFeedback(val phase: ConnectionCheckPhase = ConnectionCheckPhase.IDLE,
                                   val pcName: String = "", val message: String = "")

/** A manual check reports its result independently of the background receiver. */
class ConnectionCheckController {
    private val mutable = MutableStateFlow(ConnectionCheckFeedback())
    val feedback = mutable.asStateFlow()

    fun dismiss() { mutable.value = ConnectionCheckFeedback() }

    suspend fun check(probe: suspend () -> Connection, describe: (Throwable) -> String): Connection? {
        mutable.value = ConnectionCheckFeedback(ConnectionCheckPhase.CHECKING)
        return try {
            probe().also { mutable.value = ConnectionCheckFeedback(ConnectionCheckPhase.SUCCESS, pcName = it.name) }
        } catch (cancelled: CancellationException) {
            dismiss()
            throw cancelled
        } catch (error: Exception) {
            failed(describe(error))
        } catch (error: UnsatisfiedLinkError) {
            failed(describe(error))
        }
    }

    private fun failed(message: String): Connection? {
        mutable.value = ConnectionCheckFeedback(ConnectionCheckPhase.ERROR, message = message)
        return null
    }
}

/** A blocked notification must remain eligible after permission is granted. */
object OfferNotificationReceipt {
    fun post(id: String, lastAnnounced: String?, notify: () -> Boolean, remember: (String) -> Unit): Boolean {
        if (lastAnnounced == id || !notify()) return false
        remember(id)
        return true
    }
}
