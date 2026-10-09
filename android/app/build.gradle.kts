import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// The version comes from the repository's VERSION file (one line, MAJOR.MINOR.PATCH), the single
// source for the gateway, the image and the app. versionCode is derived from it so every release
// counts upwards: 0.5.0 -> 5000, 1.0.0 -> 1000000, 1.2.3 -> 1002003.
val versionFile = rootProject.file("../VERSION")
val versionNameFromFile = versionFile.readText().trim().lineSequence().first().trim()
val semver = Regex("""^(\d+)\.(\d+)\.(\d+)$""").find(versionNameFromFile)
    ?: error("VERSION must hold MAJOR.MINOR.PATCH, got '$versionNameFromFile'")
val (major, minor, patch) = semver.destructured.toList().map(String::toInt)
require(minor < 1000 && patch < 1000) { "MINOR and PATCH must stay below 1000 for versionCode" }
val versionCodeFromFile = major * 1_000_000 + minor * 1_000 + patch

// Android App Links: pass -PappLinksHost=mtg.example.com to the applinks flavor to make the app
// claim https links to that one gateway (docs/ANDROID.md, "App Links"). The plain flavor, the
// default, claims nothing and works with any gateway address typed at first launch.
val appLinksHost: String = (findProperty("appLinksHost") as String?)?.trim().orEmpty()

android {
    namespace = "local.mtgassistantgateway.app"
    compileSdk = 36

    defaultConfig {
        // The package ID, from the one file that holds it (docs/ANDROID.md, "The package ID").
        applicationId = file("application-id.txt").readText().trim()
        minSdk = 29
        targetSdk = 36
        versionCode = versionCodeFromFile
        versionName = versionNameFromFile
    }

    flavorDimensions += "links"
    productFlavors {
        create("plain") { dimension = "links" }
        create("applinks") {
            dimension = "links"
            manifestPlaceholders["appLinksHost"] = appLinksHost
        }
    }
    androidComponents {
        beforeVariants { variant ->
            if (variant.flavorName == "applinks" && appLinksHost.isEmpty()) {
                variant.enable = false // the flavor needs -PappLinksHost=<gateway host>
            }
        }
    }

    // Release signing, in this order: the RELEASE_* environment variables (what scripts/build.sh
    // and CI use), else an untracked android/keystore.properties with storeFile=, storePassword=,
    // keyAlias= and keyPassword= (Android Studio). Without either the release build is unsigned.
    val env = System.getenv()
    val keystoreProps = rootProject.file("keystore.properties")
    signingConfigs {
        if (!env["RELEASE_KEYSTORE"].isNullOrBlank()) {
            create("release") {
                storeFile = File(env.getValue("RELEASE_KEYSTORE")).absoluteFile
                storePassword = env["RELEASE_STORE_PASS"]
                keyAlias = env["RELEASE_KEY_ALIAS"]
                keyPassword = env["RELEASE_KEY_PASS"] ?: env["RELEASE_STORE_PASS"]
            }
        } else if (keystoreProps.exists()) {
            create("release") {
                val props = Properties().apply { keystoreProps.inputStream().use { load(it) } }
                storeFile = rootProject.file(props.getProperty("storeFile"))
                storePassword = props.getProperty("storePassword")
                keyAlias = props.getProperty("keyAlias")
                keyPassword = props.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // R8 drops the parts of CameraX and WindowManager the app does not use. The default
            // Android rules keep @JavascriptInterface methods, so the MtgNative bridge survives.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
    }

    buildFeatures { buildConfig = true } // BuildConfig.VERSION_NAME for the user agent and the bridge

    sourceSets["main"].java.srcDirs("src/main/kotlin")
    sourceSets["test"].java.srcDirs("src/test/kotlin")

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    lint { abortOnError = false }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.13.0") // ComponentActivity: a LifecycleOwner for CameraX
    implementation("androidx.browser:browser:1.10.0") // Custom Tabs: sign-in in the phone's browser (BrowserSignIn)
    implementation("androidx.camera:camera-core:1.6.2")
    implementation("androidx.camera:camera-camera2:1.6.2")
    implementation("androidx.camera:camera-lifecycle:1.6.2")
    implementation("androidx.camera:camera-view:1.6.2")
    implementation("androidx.window:window:1.5.1") // fold posture
    implementation("androidx.window:window-java:1.5.1") // ...with a callback API, no coroutines in app code
    testImplementation("junit:junit:4.13.2")
}
