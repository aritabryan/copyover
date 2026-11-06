import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.mikepenz.aboutlibraries.plugin.android")
}

// Third-party notices: generates res/raw/aboutlibraries.json for every variant.
// License texts are versioned in config/licenses so the build is reproducible
// and never depends on the network.
aboutLibraries {
    offlineMode = true
    collect {
        configPath = file("config")
        fetchRemoteLicense = false
        fetchRemoteFunding = false
        includePlatform = false
    }
    export {
        prettyPrint = false
    }
    license {
        // Fail the build if a dependency brings any other license.
        strictMode = com.mikepenz.aboutlibraries.plugin.StrictMode.FAIL
        allowedLicenses.addAll("Apache-2.0", "MIT")
    }
}

// The release key and its password live outside the repository. Without this
// file a release build comes out unsigned and prepare-update.ps1 rejects it.
val signingProperties = Properties().apply {
    val path = System.getenv("COPYOVER_SIGNING_PROPERTIES")
        ?: "${System.getProperty("user.home")}/.android/copyover-signing.properties"
    file(path).takeIf { it.isFile }?.inputStream()?.use { load(it) }
}

android {
    namespace = "dev.puente.android"
    compileSdk = 36
    defaultConfig {
        applicationId = providers.gradleProperty("copyoverApplicationId").orElse("dev.copyover.android").get()
        resValue("string", "copyover_package", applicationId!!)
        minSdk = 26
        targetSdk = 36
        versionCode = 25
        versionName = "1.0.2"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    buildFeatures { compose = true; aidl = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    signingConfigs {
        if (signingProperties.isNotEmpty()) {
            create("release") {
                storeFile = file(signingProperties.getProperty("storeFile"))
                storePassword = signingProperties.getProperty("storePassword")
                keyAlias = signingProperties.getProperty("keyAlias")
                keyPassword = signingProperties.getProperty("keyPassword")
            }
        }
    }
    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release")
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    packaging { jniLibs { useLegacyPackaging = false } }
}

dependencies {
    implementation("dev.rikka.shizuku:api:13.1.5")
    implementation("dev.rikka.shizuku:provider:13.1.5")
    implementation(platform("androidx.compose:compose-bom:2025.04.01"))
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.0")
    implementation("androidx.core:core-ktx:1.16.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation(platform("androidx.compose:compose-bom:2025.04.01"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}

// Do not silently ship a UI-only APK that crashes as soon as Rust is called.
val verifyNativeLibraries by tasks.registering {
    doLast {
        listOf("arm64-v8a", "x86_64").forEach { abi ->
            check(file("src/main/jniLibs/$abi/libpuente_core.so").isFile) {
                "Falta Rust para $abi. Ejecuta android/build.ps1 (o build-native.sh en Linux/macOS)."
            }
        }
    }
}
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("JniLibFolders") }
    .configureEach { dependsOn(verifyNativeLibraries) }

// Ships the repository's own LICENSE as assets/licenses/copyover.txt, so the
// in-app notice always matches the file at the root of the repo.
abstract class CopyOverLicense : DefaultTask() {
    @get:InputFile @get:PathSensitive(PathSensitivity.NONE) abstract val source: RegularFileProperty
    @get:OutputDirectory abstract val output: DirectoryProperty

    @TaskAction fun copy() {
        val target = output.get().dir("licenses").asFile
        target.deleteRecursively()
        target.mkdirs()
        source.get().asFile.copyTo(target.resolve("copyover.txt"), overwrite = true)
    }
}
val copyOverLicense by tasks.registering(CopyOverLicense::class) {
    source = rootProject.layout.projectDirectory.file("../LICENSE")
}
androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(copyOverLicense, CopyOverLicense::output)
    }
}
