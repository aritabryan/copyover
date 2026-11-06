package dev.puente.android

import org.json.JSONArray
import org.json.JSONObject

/** Plain in-memory model. ConnectionStore encrypts the complete document before persistence. */
data class PcCatalog(val connections: List<Connection> = emptyList(), val active: String = "") {
    fun current(): Connection = connections.firstOrNull { identity(it) == active } ?: connections.firstOrNull() ?: Connection()
    fun upsert(connection: Connection): PcCatalog {
        if (connection.key.isBlank()) throw LocalizedException(R.string.relink_pc)
        val id = identity(connection)
        return copy(connections = connections.filterNot { identity(it) == id } + connection, active = id)
    }
    fun select(id: String): PcCatalog {
        if (connections.none { identity(it) == id }) throw LocalizedException(R.string.pc_no_longer_linked)
        return copy(active = id)
    }
    fun rename(id: String, name: String): PcCatalog {
        val cleaned = name.trim()
        if (cleaned.isEmpty() || cleaned.length > 80) throw LocalizedException(R.string.pc_name_length)
        return copy(connections = connections.map { if (identity(it) == id) it.copy(name = cleaned) else it })
    }
    fun forget(id: String): PcCatalog {
        val remaining = connections.filterNot { identity(it) == id }
        return PcCatalog(remaining, if (active == id) remaining.firstOrNull()?.let(::identity).orEmpty() else active)
    }
    fun encode(): String = JSONObject().put("version", 1).put("active", active).put("pcs", JSONArray().apply {
        connections.forEach { pc -> put(JSONObject().put("id", pc.pcId).put("name", pc.name)
            .put("server", pc.server).put("last", pc.lastAddress).put("key", pc.key)
            .apply { if (pc.ca.isNotEmpty()) put("ca", pc.ca) }) }
    }).toString()

    companion object {
        fun identity(connection: Connection): String = connection.pcId.ifEmpty { "legacy:${connection.server}" }
        fun decode(raw: String): PcCatalog {
            val json = JSONObject(raw)
            require(json.getInt("version") == 1)
            val values = json.getJSONArray("pcs")
            val pcs = (0 until values.length()).map { i ->
                val value = values.getJSONObject(i)
                Connection(server = value.getString("server"), key = value.getString("key"),
                    pcId = value.getString("id"), name = value.getString("name"), lastAddress = value.getString("last"),
                    ca = value.optString("ca"))
                    .also { require(it.key.isNotBlank()) }
            }
            require(pcs.map(::identity).distinct().size == pcs.size)
            val active = json.getString("active")
            require((pcs.isEmpty() && active.isEmpty()) || pcs.any { identity(it) == active })
            return PcCatalog(pcs, active)
        }
    }
}
