# Android nativo (Kotlin + Rust)

[English](README.md) · **Español**

Android 8.0 o superior, arm64-v8a (teléfonos de 64 bits) y x86_64 (emuladores). La interfaz es Jetpack Compose; las transferencias HTTP se ejecutan en Rust mediante JNI. No utiliza WebView, no reemplaza el teclado y no necesita root, Tasker ni permisos amplios de almacenamiento.

## Interfaz

La navegación está abajo: **Historial**, **Archivos**, **Equipos** y **Ajustes**.
El historial muestra 20 copias por página, con Anterior y Siguiente siempre visibles cuando hay varias páginas.
Los textos largos usan una vista previa breve; al abrirlos o copiarlos se conserva el contenido completo.
El historial usa una sola lista desplazable, incluidos los botones
de la cabecera. Al deslizar, puedes dedicar toda la pantalla a las copias guardadas.
Cada entrada permite copiar o fijar; al tocarla se abre completa y el menú ofrece eliminarla.
La configuración y las explicaciones del historial están en Ajustes.

La portada muestra **Enviar copia**, **Recibir** y **Enviar archivo** sobre filas compactas
del historial. La papelera junto al título pide confirmación y conserva las copias fijadas.
Los estados de recepción, Shizuku y última entrega están en Ajustes.
Una PC guardada no se presenta como conectada sin comprobarlo. Los errores de vinculación siguen visibles aunque haya
PCs descubiertas. El tema claro u oscuro sigue al sistema y los botones se reorganizan
cuando se usa una fuente grande.

Esta versión conserva vínculos e historial al instalarse sobre la anterior con la
misma firma. Las pruebas de UI incluyen una lista de 30 copias en una pantalla pequeña;
necesitan un dispositivo o emulador para ejecutarse.

## Compilar en Windows

Necesitas JDK 17, Rust estable, Android SDK Platform 36, Build Tools y NDK **27.1.12297006**. Instala el SDK/NDK desde Android Studio → SDK Manager. Para los avisos de licencias de Rust hace falta `cargo-about` (`cargo install cargo-about --locked`, o el binario de sus releases en GitHub dentro de `~/.cargo/bin`). La primera compilación necesita internet para Gradle, Maven y Cargo.

```powershell
# Desde la raíz del repositorio; adapta la ruta a tu SDK.
./android/build.ps1 -Sdk C:\Android
```

El script instala los destinos Rust `aarch64-linux-android` y `x86_64-linux-android`, compila ambas bibliotecas nativas, las coloca en `app/src/main/jniLibs/` y ejecuta `assembleDebug`, `testDebugUnitTest` y `lintDebug`.

APK: `android/app/build/outputs/apk/debug/app-debug.apk`. Es una compilación debug firmada con la clave de desarrollo local, adecuada para instalar y probar. Con `-Release` también se obtiene el APK firmado en `android/app/build/outputs/apk/release/app-release.apk`; `docs/android.md` explica dónde vive la clave. No se configura publicación en Play Store.

Puedes transferir el APK al teléfono y abrirlo, permitiendo instalación desde esa app, o usar un dispositivo con depuración USB autorizado:

```powershell
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

Para trabajar desde Android Studio, abre `android/`. Compila primero Rust con `./android/build.ps1 -Sdk C:\Android -NativeOnly`. Si Studio no encuentra el SDK, configura `android/local.properties` con `sdk.dir=C:/Android`; ese archivo es local y está ignorado por Git. Las bibliotecas compiladas también están ignoradas; Gradle impide empaquetar un APK si falta alguna arquitectura.

En Linux/macOS: define `ANDROID_HOME`, instala JDK 17, Rust y el mismo SDK/NDK; ejecuta `bash android/build-native.sh`, seguido de `bash android/gradlew -p android assembleDebug testDebugUnitTest lintDebug`.

## Configurar y usar

1. Actualiza las dependencias de la PC con `python -m pip install -r requirements.txt` y reinicia el servidor con `python puente.py`.
2. Instala el APK actualizado en Android. Abre CopyOver con ambos dispositivos en el mismo Wi-Fi: aparecerá una lista de PCs. Si sale vacía, vuelve a buscar.
3. Elige tu PC. Compara el código que aparece en Android con la ventana que se abre en la PC y acepta allí. Después toca **El código coincide** en Android. Solo se hace la primera vez; no escribes IP, clave ni código y no se usa QR.
4. La recepción automática desde la PC queda activada al vincular. Copia texto o una imagen en Windows y pégalo en Android. La notificación **CopyOver · PC → Android** permite pausar. Abre CopyOver para reactivar la opción.
5. Copia texto y toca **Enviar copiado** en la notificación. Android abre un panel pequeño sobre la app en la que estás para leer el portapapeles y enviarlo. Si todo sale bien se cierra solo en un segundo; si falla, o si recibes un archivo, se queda abierto con sus opciones.
6. **Recibir** sigue disponible para transferencias manuales, incluidos archivos; ofrece **Guardar como…** y **Compartir archivo**. **Enviar archivo** abre el selector nativo para enviar desde Android.
7. Para enviar fotos o archivos desde cualquier app, usa **Compartir → CopyOver → Enviar a la PC**. También se admiten varios archivos, hasta 100 por operación.
8. Los mismos botones están fuera de la app, y funcionan aunque CopyOver esté cerrado: **Enviar** y **Recibir** como mosaicos de Ajustes rápidos (edita la cortina para agregarlos), los tres botones al mantener presionado el icono de la app, y en el widget de CopyOver de la pantalla de inicio.

La opción **Botones en la notificación** es independiente y muestra los botones de enviar y recibir.
Conservas tu teclado actual. Solo Windows → Android es automático. El servicio escribe el portapapeles
Android sin leerlo; la lectura en segundo plano sigue restringida por Android. [Restricción oficial](https://developer.android.com/about/versions/10/privacy/changes#clipboard-data).

La recepción automática requiere Windows en la PC y CopyOver actualizado en ambos dispositivos (APK 0.5.0 o posterior).
Usa un servicio foreground `connectedDevice` y una notificación persistente, sin permisos de accesibilidad,
root ni depuración. El teléfono mantiene una petición autenticada de hasta 20 segundos, que termina cuando
cambia el contador del portapapeles Windows. No descarga contenido mientras no haya cambios. Se vuelve a
comprobar la identidad de la PC antes de cada espera y solo se confirma el cursor tras copiar correctamente.
El cursor se conserva al reiniciar para no volver a aplicar una copia antigua de Windows.
Recibir manualmente reintenta hasta tres veces si falla la conexión o la PC está ocupada.
Los rechazos de vinculación o certificado se muestran sin reintentar continuamente; revisa Equipos.

Admite texto de hasta **256 KiB UTF-8**, para evitar el límite Binder del sistema, e imágenes PNG de hasta
**32 MiB**. Las imágenes se escriben como URI de FileProvider para pegarlas en apps que acepten imágenes;
los archivos copiados en el Explorador siguen siendo manuales. Las imágenes automáticas que no se usan se
eliminan; se conservan la actual y la anterior para permitir terminar una lectura ya iniciada por otra app.
Una imagen en el portapapeles y una ruta de archivo copiada en el Explorador son formatos distintos.

El servicio intenta continuar al reiniciar el teléfono, mientras la opción siga activa. Si fuerzas la
detención de CopyOver, tendrás que abrirlo de nuevo. Doze, el ahorro de batería de Samsung o una red aislada
pueden retrasar la recepción; falta verificar esos casos en un teléfono real. No se promete recepción
inmediata con el teléfono suspendido. No se borra el portapapeles Android si Windows está vacío.

Antes de vincular, Android lista PCs mediante NSD. Después de vincular y antes de enviar o recibir, busca el identificador de la PC recordada aunque cambie su IP o el puerto anunciado. **Probar conexión con la PC** repite la comprobación; **Equipos → Vincular otra PC** vuelve a la búsqueda. Los vínculos de versiones anteriores se conservan al actualizar.

La vinculación usa un compromiso SHA256 previo a revelar la clave pública efímera del teléfono. La PC fija su clave y sesión antes de esa revelación; ambos derivan el código de comparación con X25519 y HKDF-SHA256. Al aceptar en la PC se entrega la configuración cifrada con AES-GCM. Android solo la guarda después de confirmar el código. Una solicitud caduca en dos minutos y la ventana del escritorio se cierra si se ignora. Solo se muestra una ventana a la vez, con límites por dirección y por minuto.

En Windows aparece un diálogo nativo y no se abre una consola adicional. En Linux hace falta `zenity` o `kdialog` para aceptar; por ejemplo, en Fedora: `sudo dnf install zenity`. Si no están disponibles, la solicitud se rechaza y se registra el motivo.

Después de vincular, Rust comprueba un reto HMAC-SHA256 antes de enviar la clave a una dirección descubierta o guardada. La dirección verificada se fija para el ping y la transferencia.

**Cifrado.** La configuración que entrega la PC al vincular incluye su autoridad de certificados (CA) y la huella SHA-256 de esa CA; Android la guarda junto a la clave. Con ella, todo va por HTTPS (rustls) al mismo puerto y solo se acepta un certificado firmado por esa CA. El nombre del servidor no se comprueba, porque la PC cambia de IP; la cadena, las fechas y las restricciones de nombre de la CA sí. Si la PC responde con otro certificado, no se envía nada y la app avisa. Los vínculos hechos con una versión anterior de la PC no tienen CA y siguen por HTTP; la pantalla de equipos los marca **Sin cifrar** hasta que se vuelvan a vincular. La cámara y el lector QR se retiraron de la app. El comando PC `--qr` queda únicamente para versiones Android antiguas.

Si la red bloquea multicast, la lista inicial puede quedar vacía. En vínculos existentes se prueban el nombre `.local` y la última dirección conocida; una red que aísla dispositivos impide la conexión. La app no puede eliminar esas restricciones del router.

La notificación de botones manuales no requiere servicio; la recepción automática sí. Ambas se intentan
restaurar tras reiniciar o actualizar si sus opciones siguen activas. Android permite mantener el servicio
foreground aunque se deniegue mostrar notificaciones; seguirá visible en el administrador de tareas de Android.
Abre la app si el fabricante detuvo el servicio o descartaste la notificación.

## Enviar desde Android: dos opciones

En **Envío desde el celular** puedes elegir:

- **Manual**: conserva los botones de enviar y Compartir. Se usa mientras Shizuku no tenga permiso.
- **Automático con Shizuku**: observa nuevas copias de texto e imágenes y las envía a la PC vinculada.

Instala e inicia [Shizuku](https://shizuku.rikka.app/guide/setup/), abre CopyOver, selecciona el modo
automático y concede el permiso. Necesita Shizuku 13 o posterior. En Android 11 o posterior puedes
iniciarlo con depuración inalámbrica desde el propio teléfono; debes iniciarlo otra vez después de
reiniciar. En Android 8–10 normalmente requiere una PC y ADB; Android 8–9 solo admite el usuario principal.
Puedes seguir usando tu teclado. Si Shizuku se detiene, el envío manual permanece disponible.
Al autorizar Shizuku, CopyOver activa el envío automático si no habías elegido un modo.
Si lo desactivaste expresamente, conserva tu elección. La recepción desde la PC viene activada por defecto.

Al activar el modo se toma el contenido actual como referencia, sin enviarlo. Las nuevas copias de
texto admiten hasta 256 KiB UTF-8; las imágenes, hasta 32 MiB, si su proveedor permite leerlas como
shell. Los documentos siguen usando Enviar archivo o Compartir. Cuando no hay conexión se conserva solo
la última copia pendiente de envío. El historial local es independiente de esa cola. Una copia recibida desde la PC no se reenvía.
Cambiar a Manual o desactivar descarta los envíos pendientes; un envío HTTP que ya empezó puede terminar.

La recepción automática desde Windows es independiente de esta elección. **Desactivar** en la notificación
detiene ambas direcciones automáticas. Los Atajos de iPhone conservan su comportamiento.

El ayudante de Shizuku solo lee el portapapeles: no recibe la clave de la PC ni realiza conexiones.
La app sigue haciendo las transferencias mediante Rust. La lectura shell, los proveedores de imágenes,
el bloqueo de pantalla y el ahorro de batería de One UI todavía necesitan pruebas en un teléfono real.

## Versión 0.7.0

La app se organizaba en **Portapapeles**, **Archivos**, **Equipos** y **Ajustes**.

- **Historial local:** texto e imágenes que CopyOver envía, recibe o detecta con Shizuku. Copiar,
  fijar, desfijar, eliminar y borrar recientes conservando fijados. Hasta 100 recientes durante
  7 días, 20 fijados y 128 MiB de imágenes; textos de hasta 131072 caracteres. El contenido
  marcado sensible por Android no se guarda. Se puede desactivar en Ajustes, sin borrar lo guardado.
- **Estados separados:** PC → Android y Android → PC, hora de última comprobación de identidad,
  última entrega real y errores. La autorización de Shizuku se distingue de una conexión comprobada.
- **Transferencias:** bytes, tamaño y velocidad, cancelación y notificación mientras se realizan.
  La operación continúa al girar o cerrar la pantalla. Tras matar el proceso se muestra interrumpida
  y se limpian descargas incompletas al abrir de nuevo. Cancelar no revierte lo ya recibido.
- **Varias PCs:** se conservan las vinculaciones cifradas existentes; elegir una activa, renombrar
  y olvidar desde Equipos. Al compartir desde otra app se elige el destino explícitamente.
- **Shizuku:** pasos y comprobaciones dentro de Ajustes, acceso a su app y a opciones de desarrollador.
  El usuario sigue teniendo que iniciarlo y conceder permiso; no se cambia su teclado.
- **Archivos desde PC:** se anuncia una oferta y se pide aceptar antes de descargar. Mantiene el
  nombre y permite Guardar como, Compartir o Abrir. Requiere el servidor de esta versión; las ofertas
  usan la PC vinculada y su clave, sin tokens de invitado. Los archivos se escriben en disco por bloques,
  sin un tope fijo de tamaño, y se comprueba su SHA-256 antes de darlos por recibidos.

El historial queda en almacenamiento privado excluido de copias de seguridad. Las imágenes se
duplican en ese almacenamiento para no depender de la caché de una transferencia. Los archivos
recibidos siguen en caché privada; guarda una copia permanente con Guardar como. Se retienen hasta
20 recibidos. El ahorro de batería, el inicio tras reiniciar, los permisos y los proveedores de
imágenes necesitan validación en tu teléfono; la instrumentación solo se ha compilado aquí.

## Datos y límites

- Usa la misma API que los Atajos del iPhone: `/send`, `/receive`, `/ping` y el header `X-Key`.
- Los archivos usan las rutas autenticadas `/android/transferencias` y `/android/ofertas`, sin tope fijo de tamaño. Hay que actualizar la PC y Android. Ambos necesitan espacio libre; Android prepara una copia privada del archivo antes de enviarlo y conserva las descargas en su caché. Guardar como requiere espacio para una segunda copia.
- Los cortes de conexión se reintentan durante 90 segundos, continuando desde el desplazamiento del parcial. La reanudación es de la operación en curso: reiniciar la PC o matar la app obliga a empezar de nuevo. Las cancelaciones descartan el parcial pendiente y no revierten archivos ya completados.
- Texto hasta **4 MiB** por operación; el sistema puede imponer un límite menor al copiar. Las imágenes normales de hasta 32 MiB mantienen la ruta de portapapeles anterior para poder pegarlas como imagen en Windows. Las imágenes más grandes viajan como archivos. Los límites del portapapeles y del instalador APK se mantienen separados de las transferencias de archivos.
- Las URI de archivos deben ser accesibles mediante `content://`. Si una app no concede acceso al portapapeles de una imagen, usa su menú Compartir.
- Si hay archivos compartidos, se envían los archivos; no se mezcla un mensaje de texto adicional en el mismo envío.
- Los `.txt` compartidos desde Android se envían como archivos para guardarlos en Windows. Los Atajos conservan su conversión de `text/plain` a texto del portapapeles. Una imagen pequeña se copia como imagen; los archivos grandes quedan guardados en la carpeta de recepción configurada.
- Los archivos recibidos permanecen en caché privada para guardar o compartir; se retienen hasta 20 recibidos. Android también puede liberar esa caché. **Guardar como…** crea una copia permanente en la ubicación elegida.
- La clave y la CA de la PC se cifran mediante Android Keystore, se excluyen de copias de seguridad y no se registran. Con una PC vinculada con esta versión, las transferencias van por HTTPS fijado a la CA de esa PC; los vínculos anteriores siguen por HTTP **sin cifrar**, igual que los Atajos. No expongas el puerto a internet.
- La recepción automática mantiene una conexión en espera. El estado de conexión en la pantalla principal corresponde a la última comprobación manual; la notificación automática muestra su propio estado. Tras un error, el servicio busca de nuevo la PC.

## Verificar

```powershell
python -m unittest discover -s tests -v
cargo test --manifest-path android/native/Cargo.toml --locked
cargo clippy --manifest-path android/native/Cargo.toml --all-targets --locked -- -D warnings
./android/build.ps1 -Sdk C:\Android
```

Las pruebas Python sustituyen el portapapeles y los diálogos para no modificar tus datos ni abrir ventanas. Las pruebas Rust usan servidores locales, incluyendo el servidor Python real con aceptación y rechazo simulados. La búsqueda Wi-Fi, el diálogo real y las acciones Android necesitan validación en un teléfono/emulador.

## Estructura

| Ruta | Función |
|---|---|
| `app/src/main/java/dev/puente/android/` | Interfaz, configuración, permisos, portapapeles y acciones Android |
| `native/src/lib.rs` | Operaciones y autenticación HTTP |
| `native/src/multipart.rs` | Envío de archivos por streams con Content-Length |
| `native/src/response.rs` | Recepción y límites; nombres de archivo seguros |
| `native/src/sync.rs` | Recepción automática con cursor de cambios |
| `native/src/tls.rs` | HTTPS con rustls y la CA de la PC fijada al vincular |
| `app/src/main/java/dev/puente/android/AutoReceiveService.kt` | Servicio y escritura del portapapeles sin leer Android |
| `native/src/android_jni.rs` | Entrada JNI para Kotlin |
| `app/src/main/java/dev/puente/android/PcDiscovery.kt` | Búsqueda por identidad con Android NSD |
| `app/src/main/java/dev/puente/android/PairingCode.kt` | Validación del QR local de la PC |
| `build.ps1` / `build-native.sh` | Compilación cruzada con NDK y alineación de 16 KiB |
