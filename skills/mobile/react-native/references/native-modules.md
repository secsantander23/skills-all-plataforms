# Native modules and TurboModules

Two ways to write a native module. The legacy bridge uses `NativeModules` and marshals
data across an asynchronous JSON bridge. TurboModules are part of the New Architecture:
methods are invoked synchronously where the spec allows, and the module is resolved through
`TurboModuleRegistry`. Prefer TurboModules for new code.

## The spec file

Codegen reads one TypeScript spec per module. Types are restricted to a supported subset
for codegen to process them. Stick to `boolean`, `number`, `string`, plain objects,
arrays, `void`, `Promise`, callbacks, and other TurboModule specs.

```ts
// specs/NativeDeviceInfo.ts
import type { TurboModule } from 'react-native';
import { TurboModuleRegistry } from 'react-native';

export type DeviceState = {
  batteryLevel: number;
  isCharging: boolean;
};

export interface Spec extends TurboModule {
  getDeviceState(): Promise<DeviceState>;
  getBatteryLevelSync(): number;
  registerBatteryListener(callback: (level: number) => void): () => void;
}

export default TurboModuleRegistry.getEnforcing<Spec>('DeviceInfo');
```

Use `TurboModuleRegistry.get` if the module is genuinely optional on some platforms, and
null-check the result.

Enable codegen in the library `package.json`:

```json
{
  "name": "react-native-device-info-kit",
  "codegenConfig": {
    "name": "DeviceInfoSpecs",
    "type": "modules",
    "jsDir": "specs",
    "android": { "javaPackageName": "com.deviceinfokit" }
  }
}
```

Older React Native releases used `jsSrcsDir` instead of `jsDir`. Check the version's
codegen documentation for the exact key.

## Android implementation

Kotlin class implementing the generated abstract class. The generated class is named after
the spec interface minus the `Native` prefix, and the method signatures must match exactly.

```kotlin
package com.deviceinfokit

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.WritableNativeMap
import com.facebook.react.module.annotations.ReactModule

@ReactModule(name = DeviceInfoModule.NAME)
class DeviceInfoModule(reactContext: ReactApplicationContext) :
    NativeDeviceInfoSpec(reactContext) {

  companion object {
    const val NAME = "DeviceInfo"
  }

  override fun getName(): String = NAME

  override fun getDeviceState(promise: Promise) {
    val battery = reactApplicationContext.registerReceiver(
        null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
    val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
    val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
    val status = battery?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
    val map = WritableNativeMap().apply {
      putDouble("batteryLevel", if (scale > 0) level.toDouble() / scale else -1.0)
      putBoolean("isCharging", status == BatteryManager.BATTERY_STATUS_CHARGING)
    }
    promise.resolve(map)
  }

  override fun getBatteryLevelSync(): Double {
    val battery = reactApplicationContext.registerReceiver(
        null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
    val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
    val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
    return if (scale > 0) level.toDouble() / scale else -1.0
  }

  override fun registerBatteryListener(callback: (Double) -> Unit): () -> Unit {
    val receiver = object : android.content.BroadcastReceiver() {
      override fun onReceive(context: Context, intent: Intent) {
        val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
        if (scale > 0) callback(level.toDouble() / scale)
      }
    }
    reactApplicationContext.registerReceiver(
        receiver, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
    return { reactApplicationContext.unregisterReceiver(receiver) }
  }
}
```

Registration. With autolinking an app-linked package is registered automatically, but
custom or manually linked modules need a package. `BaseReactPackage` is the current
recommended base class because it participates in lazy module loading.

```kotlin
package com.deviceinfokit

import com.facebook.react.BaseReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.module.model.ReactModuleInfo
import com.facebook.react.module.model.ReactModuleInfoProvider

class DeviceInfoPackage : BaseReactPackage() {
  override fun getModule(name: String, reactContext: ReactApplicationContext): NativeModule? =
      if (name == DeviceInfoModule.NAME) DeviceInfoModule(reactContext) else null

  override fun getReactModuleInfoProvider() = ReactModuleInfoProvider {
    mapOf(
        DeviceInfoModule.NAME to
            ReactModuleInfo(
                DeviceInfoModule.NAME,
                DeviceInfoModule.NAME,
                false, // canOverrideExistingModule
                false, // needsEagerInit
                false, // isCxxModule
                true,  // isTurboModule
            ))
  }
}
```

Add it to the app if it is not autolinked:

```kotlin
// MainApplication.kt
import com.deviceinfokit.DeviceInfoPackage

override fun getPackages(): List<ReactPackage> =
    PackageList(this).packages.apply {
      add(DeviceInfoPackage())
    }
```

## iOS implementation

Objective-C++ still hosts most existing native modules. Swift is possible for the business
logic but the bridge registration lives in Objective-C++.

```objc
// DeviceInfo.h
#import <Foundation/Foundation.h>
#import <DeviceInfoSpecs/DeviceInfoSpecs.h>

NS_ASSUME_NONNULL_BEGIN

@interface RCTDeviceInfo : NSObject <NativeDeviceInfoSpec>
@end

NS_ASSUME_NONNULL_END
```

```objc
// DeviceInfo.mm
#import "DeviceInfo.h"
#import <UIKit/UIKit.h>

@implementation RCTDeviceInfo

RCT_EXPORT_MODULE(DeviceInfo)

- (NSDictionary *)deviceState {
  UIDevice *device = [UIDevice currentDevice];
  device.batteryMonitoringEnabled = YES;
  return @{
    @"batteryLevel" : @(device.batteryLevel < 0 ? -1 : device.batteryLevel),
    @"isCharging" : @(device.batteryState == UIDeviceBatteryStateCharging),
  };
}

- (void)getDeviceState:(RCTPromiseResolveBlock)resolve
               rejecter:(RCTPromiseRejectBlock)reject {
  resolve([self deviceState]);
}

- (NSNumber *)getBatteryLevelSync {
  UIDevice *device = [UIDevice currentDevice];
  device.batteryMonitoringEnabled = YES;
  return @(device.batteryLevel < 0 ? -1 : device.batteryLevel);
}

- (void)registerBatteryListener:(RCTResponseSenderBlock)onEvent {
  NSNotificationCenter *center = [NSNotificationCenter defaultCenter];
  id token = [center addObserverForName:UIDeviceBatteryLevelDidChangeNotification
                                object:nil
                                 queue:nil
                            usingBlock:^(NSNotification *_Nonnull note) {
    UIDevice *device = [UIDevice currentDevice];
    onEvent(@[@(device.batteryLevel < 0 ? -1 : device.batteryLevel)]);
  }];
  self.observerTokens = @[token];
}

@end
```

Modern React Native uses `RCTTurboModule` subclasses with `getTurboModule` when the
generated spec must be returned. If your generated spec class name differs from
`NativeDeviceInfoSpec`, read the codegen output under the build directory rather than
guessing; the generated header is printed in the Xcode build log.

For a plain Swift-only approach on the New Architecture, use the module registrar exposed
in the app delegate. The exact registration entry point has changed across releases, so
confirm against the native modules custom events page in the docs for your version.

## Events

Prefer event emitters over polling. In JS, subscribe through the spec's callback return or
through `NativeEventEmitter`.

```ts
import { NativeEventEmitter, NativeModules } from 'react-native';
import NativeDeviceInfo from '../specs/NativeDeviceInfo';

const emitter = new NativeEventEmitter(NativeModules.DeviceInfo);

const subscription = emitter.addListener('batteryLevelChanged', (level: number) => {
  console.log('battery', level);
});

// later
subscription.remove();
```

iOS requires that the emitter's argument be an `RCTEventEmitter` subclass. If you pass
`null` on iOS you get a runtime warning about missing `addListener`/`removeListeners`. The
New Architecture path uses `NativeEventEmitter` with the TurboModule directly.

## Common failures

- **`TurboModuleRegistry.getEnforcing(...): 'X' could not be found.`** The platform side
  is not registered, autolinking did not run (check `react-native config`), or the app was
  not rebuilt. Adding a file is not enough; native code requires a build.
- **Spec method mismatch at build time.** The generated abstract class demands exact
  signatures. Re-run codegen by cleaning the build; do not edit generated sources.
- **Promise never settles.** You must call `resolve` or `reject` exactly once on every
  path. Wrap the body in `try/catch` and reject.
- **Callbacks leak.** Return an unsubscribe function from listener registration and call it
  on unmount.
- **Crash on `currentActivity`.** It is null when the app is backgrounded. Always check
  before using it to start an activity or a permission flow.

## Sources

- Native modules custom events (New Architecture): https://reactnative.dev/docs/the-new-architecture/native-modules-custom-events
- Legacy native modules intro: https://reactnative.dev/docs/native-modules-intro
- TurboModuleRegistry: https://reactnative.dev/docs/turbo-module
- Codegen: https://reactnative.dev/docs/the-new-architecture/using-codegen
- Autolinking: https://reactnative.dev/docs/autolinking