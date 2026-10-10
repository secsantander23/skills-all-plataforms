# Gradle build reference

## Module layout

```
MyApp/
  settings.gradle.kts
  build.gradle.kts              root: plugins declared with apply false
  gradle.properties
  gradle/libs.versions.toml     version catalog
  gradle/wrapper/gradle-wrapper.properties
  gradlew, gradlew.bat
  app/
    build.gradle.kts
    proguard-rules.pro
    src/main/AndroidManifest.xml
    src/main/java/com/example/app/
    src/main/res/
    src/test/java/...           JVM unit tests
    src/androidTest/java/...    instrumented tests
```

## settings.gradle.kts

```kotlin
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

rootProject.name = "MyApp"
include(":app")
```

`FAIL_ON_PROJECT_REPOS` prevents a module from silently declaring its own repositories,
which is the usual source of "works on my machine" dependency resolution.

## Root build.gradle.kts

```kotlin
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.ksp) apply false
}
```

The root build file declares plugins so modules can apply them by alias. With Kotlin 2.x the
Compose compiler is its own Gradle plugin, `org.jetbrains.kotlin.plugin.compose`, instead
of the older `kotlinCompilerExtensionVersion` setting. Check the Kotlin Compose migration
notes for your Kotlin version.

## Version catalog

`gradle/libs.versions.toml`:

```toml
[versions]
agp = "8.5.2"
kotlin = "2.0.0"
composeBom = "2024.09.00"

[libraries]
androidx-core-ktx = { module = "androidx.core:core-ktx", version = "1.13.1" }
androidx-lifecycle-runtime-compose = { module = "androidx.lifecycle:lifecycle-runtime-compose", version = "2.8.6" }
androidx-compose-bom = { module = "androidx.compose:compose-bom", version.ref = "composeBom" }
androidx-compose-material3 = { module = "androidx.compose.material3:material3" }
kotlinx-coroutines-android = { module = "org.jetbrains.kotlinx:kotlinx-coroutines-android", version = "1.8.1" }
room-runtime = { module = "androidx.room:room-runtime", version = "2.6.1" }
room-compiler = { module = "androidx.room:room-compiler", version = "2.6.1" }

[plugins]
android-application = { id = "com.android.application", version.ref = "agp" }
kotlin-android = { id = "org.jetbrains.kotlin.android", version.ref = "kotlin" }
kotlin-compose = { id = "org.jetbrains.kotlin.plugin.compose", version.ref = "kotlin" }
ksp = { id = "com.google.devtools.ksp", version = "2.0.0-1.0.24" }
```

Version numbers above are examples. Resolve them from the AndroidX and Kotlin release notes
that match your project, not from memory. A BOM version without a version on the
`material3` entry resolves the whole Compose dependency set consistently.

## app/build.gradle.kts

```kotlin
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.ksp)
}

android {
    namespace = "com.example.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.example.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables.useSupportLibrary = true
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix "-debug"
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            signingConfig = signingConfigs.getByName("release")
        }
    }

    signingConfigs {
        create("release") {
            storeFile = file(System.getenv("ANDROID_KEYSTORE") ?: "release.keystore")
            storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
            keyAlias = System.getenv("ANDROID_KEY_ALIAS")
            keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.material3)

    implementation(libs.kotlinx.coroutines.android)

    implementation(libs.room.runtime)
    ksp(libs.room.compiler)

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test")
    testImplementation("app.cash.turbine:turbine")

    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
```

Notes:

- `namespace` replaces the manifest `package` attribute. It is required on AGP 8.
- `versionCode` must strictly increase for every upload to Play. `versionName` is the
  user-facing string and can repeat.
- `isMinifyEnabled` turns on R8. `isShrinkResources` removes unused resources. Both are
  defaults worth keeping on.
- `ui-test-manifest` is `debugImplementation` only. It contributes the test activity to
  debug builds and must not ship.
- `unitTests.isReturnDefaultValues = true` lets JVM tests run code that calls Android
  framework methods without stubbing them. Prefer fakes over this for behavior you care
  about.

Release signing reads credentials from the environment. Never commit a keystore.

## gradle.properties

```properties
org.gradle.jvmargs=-Xmx4g -XX:MaxMetaspaceSize=1g -Dfile.encoding=UTF-8
org.gradle.parallel=true
org.gradle.caching=true
org.gradle.configuration-cache=true
android.useAndroidX=true
android.nonTransitiveRClass=true
kotlin.code.style=official
```

- `android.useAndroidX=true` is required for AndroidX artifacts.
- `nonTransitiveRClass=true` reduces the generated `R` class size; it can break code that
  relied on transitive resource references.
- The configuration cache speeds up clean builds. Remove it temporarily if a plugin
  misbehaves.

## Commands

```bash
gradlew assembleDebug
gradlew installDebug
gradlew assembleRelease
gradlew bundleRelease          # AAB for Play
gradlew test                   # JVM unit tests
gradlew connectedAndroidTest   # instrumented tests
gradlew lint
gradlew clean
gradlew :app:dependencies
gradlew :app:dependencyInsight --dependency compose-bom --configuration debugRuntimeClasspath
gradlew tasks
gradlew --stop
gradlew --version
gradlew -q projects
```

On Windows, `gradlew.bat`. `./gradlew` also works from PowerShell in recent Gradle
versions.

## Multi-module

```
:app        application module, depends on everything else
:core       no Android UI dependencies; pure Kotlin and models
:feature-a  screen-level module with its own ViewModels
:data       Room, network, repositories
```

Rules that keep a multi-module build healthy:

- `:app` is the only module with an `applicationId`.
- Feature modules are Android libraries; they may depend on `:core` and `:data` but not on
  each other unless the dependency is a one-way edge.
- Resources moved between modules must keep unique names, or enable
  `android.nonTransitiveRClass` and fix references.
- Build variants must line up. A library published with only `release` cannot be consumed
  as `debug` by an app.

## Failure modes and fixes

| Message | Cause | Fix |
| --- | --- | --- |
| `android:exported needs to be explicitly specified for element <activity>` | Component with an intent filter and no `android:exported` | Add the attribute |
| `Manifest merger failed : uses-sdk:minSdkVersion 23 cannot be smaller than version 25 declared in library` | A dependency raised its minimum | Raise `minSdk` or downgrade the dependency |
| `Dependency requires compileSdk 34 or later` | Library compiled against a newer SDK | Raise `compileSdk` |
| `Could not resolve com.example:foo` | Wrong coordinate or missing repository | Check the coordinate, add the repository in settings |
| `Duplicate class com.foo.Bar found in modules a.jar and b.jar` | Two versions of the same library | Force alignment or exclude |
| `Execution failed for task ':app:kaptGenerateStubsDebugKotlin'` | Annotation processor failure; the real message is above it | Read the first error in the log |
| `Execution failed for task ':app:checkDebugAarMetadata'` | AAR metadata incompatibility | Usually a compileSdk or minSdk mismatch |
| `SDK location not found` | `local.properties` missing or wrong | Create `local.properties` with `sdk.dir=<path>` |
| `Plugin [id: 'com.android.application'] was not found` | Plugin repository or version catalog entry missing | Add `google()` to `pluginManagement.repositories` |
| `OutOfMemoryError` in Gradle daemon | JVM heap too small | Raise `org.gradle.jvmargs` |

Use `--stacktrace` and `--info` on failures:

```bash
gradlew assembleDebug --stacktrace
gradlew assembleDebug --info
```

## Continuous integration

```bash
gradlew clean assembleDebug test
gradlew bundleRelease
```

Run instrumented tests on an emulator started from CI, not on the host. Gradle wrapper
scripts are checked in; pin the Gradle version in
`gradle/wrapper/gradle-wrapper.properties` so CI and developers use the same build.

## Sources

- Android build configuration: https://developer.android.com/build
- Gradle version catalogs: https://docs.gradle.org/current/userguide/platforms.html
- Android Gradle plugin release notes: https://developer.android.com/build/releases/gradle-plugin
- AndroidX release notes: https://developer.android.com/jetpack/androidx/releases
- App Bundle format: https://developer.android.com/guide/app-bundle
- R8 shrinking: https://developer.android.com/build/shrink-code