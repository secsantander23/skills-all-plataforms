# Build, bundle, and debug

## Project layout

```
MyApp/
  android/
    app/build.gradle
    app/src/main/java/com/myapp/MainActivity.kt
    app/src/main/java/com/myapp/MainApplication.kt
    app/src/main/AndroidManifest.xml
    build.gradle
    settings.gradle
    gradle.properties
  ios/
    Podfile
    MyApp/AppDelegate.swift
    MyApp/Info.plist
  App.tsx
  index.js
  app.json
  metro.config.js
  react-native.config.js
  package.json
```

## Scaffolding

```bash
npx @react-native-community/cli@latest init MyApp
npx @react-native-community/cli@latest init MyApp --version 0.74.5   # pin a version
npx @react-native-community/cli@latest init MyApp --template <template-url>
```

The `init` command moved from the `react-native` package to
`@react-native-community/cli`. If you follow an older tutorial that says
`npx react-native init`, it may still work through the bundled shim, or it may fail; the
community CLI is the documented path.

## Metro

```bash
npx react-native start                 # start the bundler
npx react-native start --reset-cache   # discard the transform cache
npx react-native start --port 8082
```

Metro resolves modules and serves the JS bundle. Failure to start Metro is the most common
cause of a blank red screen on a release build.

Metro configuration for monorepos or extra resolvers:

```js
// metro.config.js
const { getDefaultConfig, mergeConfig } = require('@react-native/metro-config');

/** @type {import('metro-config').MetroConfig} */
const config = {
  resolver: {
    nodeModulesPaths: [
      require('path').resolve(__dirname, '../../node_modules'),
      require('path').resolve(__dirname, './node_modules'),
    ],
    sourceExts: ['ts', 'tsx', 'js', 'jsx', 'json'],
  },
};

module.exports = mergeConfig(getDefaultConfig(__dirname), config);
```

Run `npx react-native start` from the app directory unless the config sets
`watchFolders`. Getting this wrong produces `Unable to resolve module ... from
node_modules/...`.

## Android build

`android/gradle.properties` holds build switches:

```properties
# Enable the New Architecture (default in current releases; set explicitly if needed)
newArchEnabled=true

# Hermes is the default engine; set false only to test the JSC path
hermesEnabled=true

# Shrink resources on release builds
android.enableResourceOptimizations=true

org.gradle.jvmargs=-Xmx2048m -XX:MaxMetaspaceSize=512m
org.gradle.parallel=true
org.gradle.caching=true
```

`android/app/build.gradle` essentials:

```gradle
apply plugin: "com.android.application"
apply plugin: "org.jetbrains.kotlin.android"

android {
  namespace "com.myapp"
  compileSdk 34
  ndkVersion "26.1.10909125"

  defaultConfig {
    applicationId "com.myapp"
    minSdk 24
    targetSdk 34
    versionCode 1
    versionName "1.0.0"
  }

  signingConfigs {
    release {
      storeFile file("release.keystore")
      storePassword System.getenv("MYAPP_STORE_PASSWORD")
      keyAlias System.getenv("MYAPP_KEY_ALIAS")
      keyPassword System.getenv("MYAPP_KEY_PASSWORD")
    }
  }

  buildTypes {
    debug {
      applicationIdSuffix ".debug"
      versionNameSuffix "-debug"
    }
    release {
      signingConfig signingConfigs.release
      minifyEnabled true
      shrinkResources true
      proguardFiles getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro"
    }
  }
}

dependencies {
  implementation("com.facebook.react:react-android")
  implementation("com.facebook.react:hermes-android")
}
```

`sdkVersion` numbers above are examples. Resolve them from the library requirements of
your dependencies rather than copying them.

Run from `android/`:

```bash
gradlew assembleDebug
gradlew installDebug
gradlew bundleRelease          # AAB for Play
gradlew clean
gradlew :app:dependencies
gradlew tasks
```

On Windows, `gradlew.bat`; on POSIX shells `gradlew`. Both are checked into the template.

## iOS build

```bash
cd ios
pod install
cd ..
npx react-native run-ios
```

`Podfile` essentials:

```ruby
require Pod::Executable.execute_command('node', ['-p',
  'require.resolve(
    "react-native/scripts/react_native_pods.rb",
    {paths: [process.argv[1]]},
  )', __dir__]).strip

platform :ios, min_ios_version_supported
prepare_react_native_project!

target 'MyApp' do
  config = use_native_modules!

  use_react_native!(
    :path => config[:reactNativePath],
    :app_path => "#{Pod::Config.instance.installation_root}/.."
  )

  target 'MyAppTests' do
    inherit! :complete
  end

  post_install do |installer|
    react_native_post_install(installer, config[:reactNativePath])
  end
end
```

Always open the `.xcworkspace`, not the `.xcodeproj`, after `pod install`. Opening the
project is a classic cause of `Undefined symbols for architecture arm64` linker errors
and missing headers.

Troubleshooting commands:

```bash
xcodebuild -version
xcodebuild -workspace ios/MyApp.xcworkspace -scheme MyApp -showBuildSettings
xcodebuild -workspace ios/MyApp.xcworkspace -scheme MyApp \
  -destination 'platform=iOS Simulator,name=iPhone 15' build
xcrun simctl list devices available
pod deintegrate && pod install
```

Simulator device names differ by installed Xcode version. List them with
`xcrun simctl list devices available` rather than copying a name from a tutorial.

## Proguard and reflection

React Native release builds enable R8/minification. Keep rules for anything resolved
reflectively:

```proguard
# Keep our specs and generated model classes
-keep class com.myapp.model.** { *; }
-keep class com.facebook.react.turbomodule.** { *; }

# Standard React Native keeps
-keep class com.facebook.hermes.unicode.** { *; }
-keep class com.facebook.jni.** { *; }
```

Rule syntax above is generic. Copy the exact keep rules shipped by the libraries you use
rather than writing your own; each has its own.

## Debugging

### JavaScript

- Redbox top frame is the most useful signal.
- `console.log` and `console.warn`; logs are stripped in release by the minifier in many
  templates.
- React Native DevTools in current releases gives the element tree, props, styles, network,
  and a CPU profiler. Older releases used the Flipper integration. Check which applies to
  your version.
- Place a breakpoint with a `debugger;` statement, or set a breakpoint in the dev tools
  and reload.

### Native logs

```bash
npx react-native log-android      # adb logcat filtered
npx react-native log-ios
adb logcat -s ReactNativeJS:V ReactNative:V
xcrun simctl spawn booted log stream --predicate 'processImagePath CONTAINS "MyApp"'
```

### Gradle

```bash
gradlew assembleDebug --stacktrace
gradlew assembleDebug --info | Select-String "config"   # PowerShell
gradlew --stop
gradlew clean
```

On failure to find a native dependency, check `npx react-native config` for autolinking
metadata, then confirm the Android module was included in the Gradle build.

### CocoaPods

```bash
pod repo update
pod install --verbose
pod deintegrate
rm -rf Pods Podfile.lock
```

`[!] The following Swift compiler flags are incompatible with the current CocoaPods
installation` and circular dependency errors are usually solved by updating the CocoaPods
gem, not by editing the Podfile.

### Common runtime errors

| Message | Cause | Fix |
| --- | --- | --- |
| `Unable to resolve module X` | Missing dependency or bad Metro config | Install the package, restart Metro, check `watchFolders` |
| `Invariant Violation: X could not be found` | Native module not registered or not built | Rebuild the app, check autolinking |
| `NullPointerException: ... entry to access` | Stale APK after native change | `gradlew clean` then rebuild |
| `Undefined symbols for architecture arm64` | Opened `.xcodeproj` instead of `.xcworkspace` | Run `pod install`, open the workspace |
| `Duplicate class com.foo.Bar` | Two libraries ship the same class | Exclude one via `packagingOptions` or dependency constraint |
| `Execution failed for task ':app:checkDebugDuplicateClasses'` | Dependency pulled two versions | Align versions in `build.gradle` |
| `Manifest merger failed` | Conflicting manifest nodes | Add `tools:node="replace|remove"` in `android/app/src/main/AndroidManifest.xml` |

## Continuous integration

```bash
npm ci
npm run lint
npm test
cd android && ./gradlew assembleDebug
```

For iOS in CI, use `xcodebuild -workspace ... -scheme ... -destination
'generic/platform=iOS Simulator' build` to avoid needing a specific simulator. Archive
with `-archivePath` plus `-allowProvisioningUpdates` only if your CI holds valid
credentials; otherwise supply explicit code signing settings.

EAS Build is the mainstream hosted option for React Native:

```bash
npm install -g eas-cli
eas login
eas build:configure
eas build --platform ios --profile production
```

`eas.json` defines build profiles with `distribution`, `channel`, and platform settings.

## Sources

- Building and running: https://reactnative.dev/docs/running-on-android
- Metro configuration: https://reactnative.dev/docs/metro
- Android build without Android Studio: https://reactnative.dev/docs/environment-setup
- Debugging: https://reactnative.dev/docs/debugging
- Performance: https://reactnative.dev/docs/performance-overview
- React Native Upgrade Helper: https://react-native-community.github.io/upgrade-helper/