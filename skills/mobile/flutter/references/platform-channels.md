# Platform channels in detail

A platform channel is a named, typed pipe between Dart and host code. Names must match
exactly on both sides. Values are encoded by the standard codec, which supports `null`,
`bool`, `int`, `double`, `String`, `Uint8List`, `List`, and `Map`, plus a few special
values (`FlutterStandardTypedData` for typed buffers). Custom classes do not cross the
boundary: convert to maps or lists.

## Channel types

| Type | Direction | Use |
| --- | --- | --- |
| `MethodChannel` | Both | Request and response, including `invokeMethod` returning a `Future` |
| `EventChannel` | Host to Dart | Ongoing stream of events; one listener at a time per channel |
| `BasicMessageChannel` | Both | Generic messaging with a custom codec, used by framework internals |

Prefer `MethodChannel` for request/response, `EventChannel` for streams. Reach for
`BasicMessageChannel` only when you control both ends and need a custom codec.

## A Flutter plugin package layout

```
my_plugin/
  lib/my_plugin.dart
  android/build.gradle
  android/src/main/kotlin/com/example/my_plugin/MyPlugin.kt
  ios/Classes/MyPluginPlugin.h
  ios/Classes/MyPluginPlugin.m
  ios/Classes/MyPluginPlugin.swift
  pubspec.yaml
```

`pubspec.yaml`:

```yaml
name: my_plugin
description: Reads device telemetry.
version: 0.1.0
publish_to: none

environment:
  sdk: ">=3.0.0 <4.0.0"
  flutter: ">=3.0.0"

dependencies:
  flutter:
    sdk: flutter

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^3.0.0

flutter:
  plugin:
    platforms:
      android:
        package: com.example.my_plugin
        pluginClass: MyPlugin
      ios:
        pluginClass: MyPluginPlugin
```

iOS plugin registration for a Swift class uses `.swift` and registers through the
generated registrar. When a Swift plugin needs an Objective-C shim, the `pluginClass`
must match the Objective-C class name, and the Swift class must be annotated
`@objc(MyPluginPlugin)` and subclass `FlutterPlugin`.

## Android plugin registration

```kotlin
package com.example.my_plugin

import com.example.my_plugin.telemetry.TelemetryPlugin
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import io.flutter.embedding.engine.plugins.activity.ActivityAware
import io.flutter.embedding.engine.plugins.activity.ActivityPluginBinding

class MyPlugin : FlutterPlugin, MethodChannel.MethodCallHandler, ActivityAware {
  private var channel: MethodChannel? = null
  private var binding: ActivityPluginBinding? = null

  override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
    channel = MethodChannel(binding.binaryMessenger, "com.example.app/battery")
    channel?.setMethodCallHandler(this)
  }

  override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
    channel?.setMethodCallHandler(null)
    channel = null
  }

  override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
    when (call.method) {
      "getBatteryLevel" -> result.success(TelemetryPlugin.batteryLevel(binding?.activity))
      "getDeviceId" -> result.success(TelemetryPlugin.deviceId())
      else -> result.notImplemented()
    }
  }

  override fun onAttachedToActivity(binding: ActivityPluginBinding) {
    this.binding = binding
  }

  override fun onDetachedFromActivityForConfigChanges() {
    binding = null
  }

  override fun onReattachedToActivityForConfigChanges(binding: ActivityPluginBinding) {
    this.binding = binding
  }

  override fun onDetachedFromActivity() {
    binding = null
  }
}
```

Always implement `onDetachedFromEngine` and clear the handler. A leaked handler keeps the
engine alive and logs `Attempt to invoke method on a destroyed channel` on the next call.

`result.notImplemented()` is required for unknown methods. Omitting it produces
`Not implemented` errors for any call the Dart side adds later.

## iOS plugin registration

Objective-C registrar shim:

```objc
// MyPluginPlugin.m
#import "MyPluginPlugin.h"
#if __has_include(<my_plugin/my_plugin-Swift.h>)
#import <my_plugin/my_plugin-Swift.h>
#else
#import "my_plugin-Swift.h"
#endif

@implementation MyPluginPlugin
+ (void)registerWithRegistrar:(NSObject<FlutterPluginRegistrar> *)registrar {
  FlutterMethodChannel *channel =
      [FlutterMethodChannel methodChannelWithName:@"com.example.app/battery"
                                  binaryMessenger:[registrar messenger]];
  MyPluginPlugin *instance = [[MyPluginPlugin alloc] initWithChannel:channel];
  [registrar addMethodCallDelegate:instance channel:channel];
  [registrar publish:instance];
}
@end
```

The Swift implementation:

```swift
import Flutter
import UIKit

public class MyPlugin: NSObject, FlutterPlugin, FlutterStreamHandler {
  private var sink: FlutterEventSink?
  private let channel: FlutterMethodChannel

  init(channel: FlutterMethodChannel) {
    self.channel = channel
    super.init()
  }

  public static func register(with registrar: FlutterPluginRegistrar) {
    let channel = FlutterMethodChannel(
      name: "com.example.app/battery",
      binaryMessenger: registrar.messenger()
    )
    let instance = MyPlugin(channel: channel)
    registrar.addMethodCallDelegate(instance, channel: channel)
  }

  public func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "getDeviceId":
      result(UIDevice.current.identifierForVendor?.uuidString ?? "")
    case "getBatteryLevel":
      UIDevice.current.isBatteryMonitoringEnabled = true
      let level = UIDevice.current.batteryLevel
      if level < 0 {
        result(FlutterError(code: "UNAVAILABLE",
                            message: "Battery level unavailable",
                            details: nil))
      } else {
        result(level)
      }
    default:
      result(FlutterMethodNotImplemented)
    }
  }

  public func onListen(
    withArguments arguments: Any?,
    eventSink events: @escaping FlutterEventSink
  ) -> FlutterError? {
    sink = events
    return nil
  }

  public func onCancel(withArguments arguments: Any?) -> FlutterError? {
    sink = nil
    return nil
  }
}
```

The `__has_include` dance in the `.m` file handles static versus framework-style
integration. Copy it from Flutter's own plugin template rather than retyping it.

## Typed arguments and results in Dart

```dart
class Telemetry {
  const Telemetry._();

  static const MethodChannel _method = MethodChannel('com.example.app/battery');
  static const EventChannel _events = EventChannel('com.example.app/battery/level');

  static Future<DeviceSnapshot> snapshot() async {
    final Map<Object?, Object?> raw =
        await _method.invokeMapMethod<Object?, Object?>('snapshot') ??
            const <Object?, Object?>{};
    return DeviceSnapshot(
      batteryLevel: (raw['batteryLevel'] as num?)?.toDouble() ?? -1,
      deviceId: raw['deviceId'] as String? ?? '',
    );
  }

  static Stream<double> levels() =>
      _events.receiveBroadcastStream().map((dynamic event) => (event as num).toDouble());
}

class DeviceSnapshot {
  const DeviceSnapshot({required this.batteryLevel, required this.deviceId});

  final double batteryLevel;
  final String deviceId;
}
```

Use `invokeMapMethod`, `invokeListMethod`, `invokeMethod<T>`, and friends for typed
results. `invokeMethod` without a type parameter returns `dynamic`, which pushes null
checking to runtime.

Handle platform errors where they can be handled:

```dart
try {
  final double level = await Telemetry.level();
  setLevel(level);
} on PlatformException catch (error) {
  if (error.code == 'UNAVAILABLE') {
    setLevel(-1);
  } else {
    showError(error.message ?? 'Unknown error');
  } on MissingPluginException {
  // Plugin not registered on this platform; fall back silently.
  setLevel(-1);
}
```

## Background isolate channels

Long work on the UI isolate causes dropped frames. Run it in an isolate and communicate
over a channel.

```dart
import 'dart:isolate';

class StatsResult {
  const StatsResult({required this.mean, required this.count});

  final double mean;
  final int count;
}

StatsResult _computeStats(List<double> values) {
  double total = 0;
  for (final double v in values) {
    total += v;
  }
  return StatsResult(
    mean: values.isEmpty ? 0 : total / values.length,
    count: values.length,
  );
}

Future<StatsResult> computeStats(List<double> values) {
  return compute(_computeStats, values);
}
```

`compute` requires a top-level or static function, and the message must be sendable across
the isolate boundary. Object references, closures capturing non-const state, and open
handles do not transfer.

## Testing channels from Dart

```dart
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const MethodChannel channel = MethodChannel('com.example.app/battery');
  final List<MethodCall> log = <MethodCall>[];

  setUp(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (MethodCall call) async {
      log.add(call);
      if (call.method == 'getBatteryLevel') return 0.42;
      return null;
    });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
    log.clear();
  });

  test('returns the platform value', () async {
    expect(await Telemetry.level(), 0.42);
    expect(log.single.method, 'getBatteryLevel');
  });
}
```

The mock handler must be removed in `tearDown` or it leaks into other tests in the same
file.

## Failure modes

- **`MissingPluginException`**: handler never registered, app not rebuilt after adding the
  plugin, or the channel name differs by a character.
- **`PlatformException` with `UNAVAILABLE`**: the platform side returned an error. Log
  `error.code`, `error.message`, and `error.details`.
- **Calls stop working after an Activity restart**: `onDetachedFromActivityForConfigChanges`
  not implemented, leaving a stale activity reference.
- **`Not implemented` on a method that exists**: the Dart and native names differ, or the
  native switch falls through to `notImplemented`.
- **EventChannel delivers nothing**: the host never calls `events(success(value))`, or the
  stream handler was cancelled.
- **Native crashes on a background thread**: Kotlin and Swift require platform-specific
  dispatching rules. Send results on the main thread.

## Sources

- Platform channels overview: https://docs.flutter.dev/platform-integration/platform-channels
- MethodChannel: https://api.flutter.dev/flutter/services/MethodChannel-class.html
- EventChannel: https://api.flutter.dev/flutter/services/EventChannel-class.html
- Writing a platform-specific plugin: https://docs.flutter.dev/platform-integration/platform-channels
- Platform channels in an existing app: https://docs.flutter.dev/platform-integration
- Background processing: https://docs.flutter.dev/background-processing
- Dart isolates: https://dart.dev/language/concurrency