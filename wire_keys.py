"""Names of the JSON fields that travel between CopyOver installs (PC to PC and PC to Android).

These fields used to have Spanish names, and a PC or phone not yet updated still reads and writes them.
Everything inside the program uses the English name: incoming data goes through `from_wire` and outgoing
data through `to_wire`, which also sends the old name."""
import re

OLD_WIRE_NAMES = {
    "archivos": "files",
    "chasis": "chassis",
    "cifrado": "encrypted",
    "clave_publica": "public_key",
    "codigo": "code",
    "compromiso": "commitment",
    "de": "sender",
    "empareja": "pairing",
    "estado": "status",
    "nombre": "name",
    "oferta": "offer",
    "preparando": "preparing",
    "prueba": "proof",
    "reanudar": "resume",
    "recibido": "received",
    "sesion": "session",
    "tamano": "size",
}
_NEW_TO_OLD = {new: old for old, new in OLD_WIRE_NAMES.items()}

# Routes, headers and query parameters that had Spanish names. The PC serves the English ones and turns the old
# ones into them as a request arrives, so everything after that only sees English. What one install sends to
# another keeps using the old route, the only one a PC not yet updated understands, and carries both headers.
OLD_ROUTES = {
    "/android/actualizacion": "/android/update",
    "/android/ofertas": "/android/offers",
    "/android/transferencias": "/android/transfers",
    "/configurar": "/configure",
    "/dispositivo/tipo": "/device/type",
    "/enviar": "/send",
    "/identidad": "/identity",
    "/invitado": "/guest",
    "/invitado/confirmar-recepcion": "/guest/confirm-receipt",
    "/invitado/descargar": "/guest/download",
    "/invitado/estado": "/guest/status",
    "/invitado/quien": "/guest/who",
    "/invitado/subir": "/guest/upload",
    "/invitado/texto": "/guest/text",
    "/local/aviso": "/local/notice",
    "/local/enviar": "/local/send",
    "/p2p/cancelar": "/p2p/cancel",
    "/p2p/emparejar": "/p2p/pair",
    "/p2p/emparejar/v2": "/p2p/pair/v2",
    "/p2p/emparejar/v2/confirmar": "/p2p/pair/v2/confirm",
    "/p2p/emparejar/v2/inicio": "/p2p/pair/v2/start",
    "/p2p/emparejar/v2/revelar": "/p2p/pair/v2/reveal",
    "/p2p/equipos": "/p2p/peers",
    "/p2p/estado": "/p2p/status",
    "/p2p/ofrecer": "/p2p/offer",
    "/p2p/perfil": "/p2p/profile",
    "/p2p/reanudar": "/p2p/resume",
    "/p2p/subir": "/p2p/upload",
    "/traer": "/receive",
    "/traer/tipo": "/receive/type",
    "/vincular": "/link",
    "/vincular/codigo": "/link/code",
    "/vincular/estado": "/link/status",
}
_OLD_ROUTES_LONGEST_FIRST = sorted(OLD_ROUTES, key=len, reverse=True)
_NEW_ROUTES_LONGEST_FIRST = sorted(OLD_ROUTES.values(), key=len, reverse=True)
_ROUTE_NEW_TO_OLD = {new: old for old, new in OLD_ROUTES.items()}
_OLD_ANDROID_ACTIONS = {"archivo": "file", "cancelar": "cancel", "confirmar": "confirm", "estado": "status"}
_ANDROID_ACTION_NEW_TO_OLD = {new: old for old, new in _OLD_ANDROID_ACTIONS.items()}
OLD_HEADERS = {
    "X-Clave": "X-Key",
    "X-Dispositivo": "X-Device",
    "X-Modelo": "X-Device-Model",
    "X-Nombre-Archivo": "X-File-Name",
    "X-Nombre-Dispositivo": "X-Device-Name",
    "X-Puente-Offset": "X-CopyOver-Offset",
    "X-Secreto-Local": "X-Local-Secret",
    "X-Vinculo": "X-Link-Session",
}
_HEADER_NEW_TO_OLD = {new: old for old, new in OLD_HEADERS.items()}
OLD_QUERY_NAMES = {"desde": "since", "disp": "device", "huella": "fingerprint", "modelo": "model", "reto": "challenge",
                   "soy": "me", "tipo": "type"}
_OLD_QUERY = re.compile(r"(^|&)(" + "|".join(OLD_QUERY_NAMES) + r")=")


def from_wire(value):
    """A copy of `value` using the current names. If both names came, the current one wins."""
    if isinstance(value, list):
        return [from_wire(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        new_name = OLD_WIRE_NAMES.get(key)
        if new_name is None:
            result[key] = from_wire(item)
        elif new_name not in value:
            result[new_name] = from_wire(item)
    return result


def to_wire(value):
    """A copy of `value` carrying each field under its current name and its old one."""
    if isinstance(value, list):
        return [to_wire(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        result[key] = to_wire(item)
        if key in _NEW_TO_OLD:
            result[_NEW_TO_OLD[key]] = result[key]
    return result


def _swap_prefix(path, names, table):
    for name in names:
        if path == name or path.startswith(name + "/"):
            return table[name] + path[len(name):]
    return path


def _swap_android_action(path, table):
    start, _, action = path.rpartition("/")
    if action in table and start.startswith(("/android/transfers/", "/android/offers/",
                                             "/android/transferencias/", "/android/ofertas/")):
        return f"{start}/{table[action]}"
    return path


def route_from_old(path):
    """The English route for one that may use an old name. Anything else comes back as it was."""
    return _swap_android_action(_swap_prefix(path, _OLD_ROUTES_LONGEST_FIRST, OLD_ROUTES), _OLD_ANDROID_ACTIONS)


def route_to_old(path):
    """The name a PC not yet updated understands, for a request going to another install."""
    return _swap_android_action(_swap_prefix(path, _NEW_ROUTES_LONGEST_FIRST, _ROUTE_NEW_TO_OLD), _ANDROID_ACTION_NEW_TO_OLD)


def query_from_old(query):
    """A query string with its parameters under their English names; their values aren't touched."""
    return _OLD_QUERY.sub(lambda m: f"{m[1]}{OLD_QUERY_NAMES[m[2]]}=", query)


def headers_to_wire(headers):
    """A copy of `headers` that also carries the old name of each one that had another."""
    result = dict(headers)
    for name, value in headers.items():
        if name in _HEADER_NEW_TO_OLD:
            result[_HEADER_NEW_TO_OLD[name]] = value
    return result
