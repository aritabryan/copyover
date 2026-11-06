package dev.puente.android

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class TransferViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = TransferRepository(application)
    private val autoConnection = AutoConnection(application)
    val state = TransferRecords.state
    val focusGate = FocusGate()
    init { TransferRecords.initialize(application) }
    private fun text(id: Int): String = getApplication<Application>().getString(id)

    fun send(outgoing: Outgoing, target: Connection? = null) {
        val connection = target ?: ConnectionStore(getApplication()).load()
        TransferService.start(getApplication(), connection, outgoing)
    }
    fun receive() {
        val connection = ConnectionStore(getApplication()).load()
        TransferService.start(getApplication(), connection)
    }
    fun cancel() = TransferService.cancel(getApplication())
    private var currentState: TransferState
        get() = state.value
        set(value) { TransferRecords.update(getApplication(), value) }

    fun fail(message: String) {
        currentState = TransferState(finished = true, failed = true, message = message)
    }

    fun textCopied() {
        currentState = currentState.copy(textCopied = true, message = text(R.string.text_copied_from_pc))
    }

    fun save(destination: Uri) {
        val file = currentState.result?.file ?: return
        transfer(preserveResult = true) {
            withContext(Dispatchers.IO) { repository.save(file, destination) }
            TransferResult(text(R.string.file_saved), file = file)
        }
    }

    private fun transfer(preserveResult: Boolean = false, operation: suspend () -> TransferResult) {
        if (currentState.busy) return
        val previous = if (preserveResult) currentState.result else null
        currentState = TransferState(busy = true, message = text(R.string.transferring), result = previous)
        viewModelScope.launch {
            try {
                val result = operation()
                currentState = TransferState(finished = true, message = result.message, result = result)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                currentState = TransferState(finished = true, failed = true,
                    message = getApplication<Application>().describe(error, R.string.transfer_failed), result = previous)
            } catch (_: UnsatisfiedLinkError) {
                fail(text(R.string.install_incomplete))
            }
        }
    }
}
