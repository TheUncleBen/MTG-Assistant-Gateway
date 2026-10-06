// The Android app's Gradle project. Open android/ in Android Studio, or build from the command
// line with ./gradlew (scripts/build.sh fetches the Android SDK when none is installed).
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "mtg-assistant-gateway"
include(":app")
