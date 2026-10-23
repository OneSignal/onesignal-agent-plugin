# iOS Notification Service Extension and App Group

Reference for the `setup` skill, loaded from [ios.md](ios.md). The Notification Service Extension (NSE) and the App Group are part of the default native iOS install: images, action buttons, badge counts, and confirmed delivery all run inside the extension, and the extension reads the SDK's state through the App Group. Without them a plain push still arrives, so the omission is silent. `<plugin>` has the meaning [SKILL.md](SKILL.md) defines.

## Required outcomes

- The `OneSignalNotificationServiceExtension` target exists and builds.
- The NSE target links `OneSignalExtension` (SPM) or the `OneSignal/OneSignal` pod (CocoaPods).
- The App Group `group.<MAIN_APP_BUNDLE_ID>.onesignal` is in the entitlements of **both** the main app target and the NSE target, with the same string.
- The NSE deployment target equals the main app target's.
- `NotificationService` forwards to `OneSignalExtension.didReceiveNotificationExtensionRequest`.
- The main app embeds the `.appex` in an `Embed Foundation Extensions` build phase.

## App Group rules

Build the group name from the **main app target's** Bundle ID, never the extension's:

- Correct: `group.com.example.myapp.onesignal`
- Wrong: `group.com.example.myapp.OneSignalNotificationServiceExtension.onesignal`

If the project already uses a different App Group for OneSignal, keep it, and add `OneSignal_app_groups_key` with that name to the `Info.plist` of both targets. With automatic signing, Xcode registers the group with the Apple team on the next device build; a physical-device build still needs a valid team.

## Files — write the 3 templates as-is

Create `OneSignalNotificationServiceExtension/` next to the `.xcodeproj` and write:

| Template | Destination | Substitution |
|---|---|---|
| [assets/ios/NotificationService.swift.tmpl](assets/ios/NotificationService.swift.tmpl) | `OneSignalNotificationServiceExtension/NotificationService.swift` | none |
| [assets/ios/OneSignalNotificationServiceExtension-Info.plist.tmpl](assets/ios/OneSignalNotificationServiceExtension-Info.plist.tmpl) | `OneSignalNotificationServiceExtension/Info.plist` | none |
| [assets/ios/OneSignalNotificationServiceExtension.entitlements.tmpl](assets/ios/OneSignalNotificationServiceExtension.entitlements.tmpl) | `OneSignalNotificationServiceExtension/OneSignalNotificationServiceExtension.entitlements` | `__MAIN_APP_BUNDLE_ID__` → the main app target's Bundle ID |

Do not hand-write `NotificationService.swift`: the plugin repository compile-checks the template against a stub of `OneSignalExtension.h`, and the 2 calls in it are the only ones the extension needs. Use Swift unless the project is Objective-C only; for an Objective-C project, set `NSExtensionPrincipalClass` to `NotificationService` without the `$(PRODUCT_MODULE_NAME).` prefix.

Add the same App Group to the **main app** `.entitlements`, next to `aps-environment`:

```xml
<key>com.apple.security.application-groups</key>
<array>
	<string>group.MAIN_APP_BUNDLE_ID.onesignal</string>
</array>
```

## Add the NSE target to `project.pbxproj`

Prefer the text edit: the user's host usually has no Xcode UI, and the objects below are what File ▸ New ▸ Target writes. Preview them in the Step-5 diff like every other pbxproj edit (safety contract §5). If the pbxproj format does not match the shapes below, stop the edit and hand the target creation to the human (File ▸ New ▸ Target ▸ Notification Service Extension, named `OneSignalNotificationServiceExtension`); then still write the 3 files, the App Group, and the product link, and report `setup.platform_config fail capability_manual` ([ios.md](ios.md) → Checkpoint).

Every object needs a unique 24-character hex ID. The placeholders `NSE0000000000000000000X` below stand for IDs you generate; check that none collides with an existing ID. Replace `MAIN_APP_BUNDLE_ID`, `<PROJECT_OBJECT_ID>`, and the deployment target with the project's values.

### 1. Product reference

```text
NSE0000000000000000000A /* OneSignalNotificationServiceExtension.appex */ = {isa = PBXFileReference; explicitFileType = "wrapper.app-extension"; includeInIndex = 0; path = OneSignalNotificationServiceExtension.appex; sourceTree = BUILT_PRODUCTS_DIR; };
```

Add the reference to the `children` list of the `Products` `PBXGroup`.

### 2. Native target and build phases

```text
NSE0000000000000000000E /* OneSignalNotificationServiceExtension */ = {
	isa = PBXNativeTarget;
	buildConfigurationList = NSE0000000000000000000F /* Build configuration list */;
	buildPhases = (
		NSE0000000000000000000B /* Sources */,
		NSE0000000000000000000C /* Frameworks */,
		NSE0000000000000000000D /* Resources */,
	);
	buildRules = (
	);
	dependencies = (
	);
	name = OneSignalNotificationServiceExtension;
	productName = OneSignalNotificationServiceExtension;
	productReference = NSE0000000000000000000A /* OneSignalNotificationServiceExtension.appex */;
	productType = "com.apple.product-type.app-extension";
};
NSE0000000000000000000B /* Sources */ = {
	isa = PBXSourcesBuildPhase;
	buildActionMask = 2147483647;
	files = (
	);
	runOnlyForDeploymentPostprocessing = 0;
};
NSE0000000000000000000C /* Frameworks */ = {
	isa = PBXFrameworksBuildPhase;
	buildActionMask = 2147483647;
	files = (
	);
	runOnlyForDeploymentPostprocessing = 0;
};
NSE0000000000000000000D /* Resources */ = {
	isa = PBXResourcesBuildPhase;
	buildActionMask = 2147483647;
	files = (
	);
	runOnlyForDeploymentPostprocessing = 0;
};
```

Add `NSE0000000000000000000E` to the `targets` list of the `PBXProject` object.

### 3. File group for the NSE folder

**Xcode 16+ synchronized-group projects** (the pbxproj already has `PBXFileSystemSynchronizedRootGroup` objects): add a synchronized group, and exclude `Info.plist` and the entitlements file from the resource copy, or the build fails with "Multiple commands produce Info.plist".

```text
NSE00000000000000000012 /* OneSignalNotificationServiceExtension */ = {
	isa = PBXFileSystemSynchronizedRootGroup;
	exceptions = (
		NSE00000000000000000013 /* PBXFileSystemSynchronizedBuildFileExceptionSet */,
	);
	path = OneSignalNotificationServiceExtension;
	sourceTree = "<group>";
};
NSE00000000000000000013 /* PBXFileSystemSynchronizedBuildFileExceptionSet */ = {
	isa = PBXFileSystemSynchronizedBuildFileExceptionSet;
	membershipExceptions = (
		Info.plist,
		OneSignalNotificationServiceExtension.entitlements,
	);
	target = NSE0000000000000000000E /* OneSignalNotificationServiceExtension */;
};
```

Add the group to the main `PBXGroup`'s `children`, and set `fileSystemSynchronizedGroups = ( NSE00000000000000000012 /* OneSignalNotificationServiceExtension */, );` on the NSE native target.

**Classic projects** (`PBXGroup` / `PBXFileReference`): add a `PBXFileReference` for each of the 3 files, group them in a `PBXGroup` under the main group, and add a `PBXBuildFile` for `NotificationService.swift` to the NSE `Sources` phase's `files`. Do not add `Info.plist` or the entitlements file to any build phase.

### 4. Build configurations

Copy `IPHONEOS_DEPLOYMENT_TARGET`, `SWIFT_VERSION`, `DEVELOPMENT_TEAM`, and the signing style from the main app target. Debug and Release are identical except for `name`.

```text
NSE00000000000000000010 /* Debug */ = {
	isa = XCBuildConfiguration;
	buildSettings = {
		CODE_SIGN_ENTITLEMENTS = OneSignalNotificationServiceExtension/OneSignalNotificationServiceExtension.entitlements;
		CODE_SIGN_STYLE = Automatic;
		CURRENT_PROJECT_VERSION = 1;
		GENERATE_INFOPLIST_FILE = YES;
		INFOPLIST_FILE = OneSignalNotificationServiceExtension/Info.plist;
		IPHONEOS_DEPLOYMENT_TARGET = 17.0; /* the MAIN APP target's value */
		LD_RUNPATH_SEARCH_PATHS = (
			"$(inherited)",
			"@executable_path/Frameworks",
			"@executable_path/../../Frameworks",
		);
		MARKETING_VERSION = 1.0;
		PRODUCT_BUNDLE_IDENTIFIER = MAIN_APP_BUNDLE_ID.OneSignalNotificationServiceExtension;
		PRODUCT_NAME = "$(TARGET_NAME)";
		SKIP_INSTALL = YES;
		SWIFT_EMIT_LOC_STRINGS = YES;
		SWIFT_VERSION = 5.0;
		TARGETED_DEVICE_FAMILY = "1,2";
	};
	name = Debug;
};
NSE0000000000000000000F /* Build configuration list for PBXNativeTarget "OneSignalNotificationServiceExtension" */ = {
	isa = XCConfigurationList;
	buildConfigurations = (
		NSE00000000000000000010 /* Debug */,
		NSE00000000000000000011 /* Release */,
	);
	defaultConfigurationIsVisible = 0;
	defaultConfigurationName = Release;
};
```

### 5. Embed the extension in the main app target

```text
NSE00000000000000000016 /* PBXContainerItemProxy */ = {
	isa = PBXContainerItemProxy;
	containerPortal = <PROJECT_OBJECT_ID> /* Project object */;
	proxyType = 1;
	remoteGlobalIDString = NSE0000000000000000000E;
	remoteInfo = OneSignalNotificationServiceExtension;
};
NSE00000000000000000017 /* PBXTargetDependency */ = {
	isa = PBXTargetDependency;
	target = NSE0000000000000000000E /* OneSignalNotificationServiceExtension */;
	targetProxy = NSE00000000000000000016 /* PBXContainerItemProxy */;
};
NSE00000000000000000014 /* OneSignalNotificationServiceExtension.appex in Embed Foundation Extensions */ = {isa = PBXBuildFile; fileRef = NSE0000000000000000000A /* OneSignalNotificationServiceExtension.appex */; settings = {ATTRIBUTES = (RemoveHeadersOnCopy, ); }; };
NSE00000000000000000015 /* Embed Foundation Extensions */ = {
	isa = PBXCopyFilesBuildPhase;
	buildActionMask = 2147483647;
	dstPath = "";
	dstSubfolderSpec = 13;
	files = (
		NSE00000000000000000014 /* OneSignalNotificationServiceExtension.appex in Embed Foundation Extensions */,
	);
	name = "Embed Foundation Extensions";
	runOnlyForDeploymentPostprocessing = 0;
};
```

On the **main app** target, append `NSE00000000000000000015 /* Embed Foundation Extensions */` to `buildPhases` after `Resources`, and `NSE00000000000000000017 /* PBXTargetDependency */` to `dependencies`.

### 6. Link the OneSignal product to the NSE target

**SPM:**

```text
NSE00000000000000000008 /* OneSignalExtension */ = {
	isa = XCSwiftPackageProductDependency;
	package = <ONESIGNAL_PACKAGE_REFERENCE_ID> /* XCRemoteSwiftPackageReference "OneSignal-XCFramework" */;
	productName = OneSignalExtension;
};
NSE00000000000000000009 /* OneSignalExtension in Frameworks */ = {isa = PBXBuildFile; productRef = NSE00000000000000000008 /* OneSignalExtension */; };
```

Add the `PBXBuildFile` to the NSE `Frameworks` phase's `files`, and `packageProductDependencies = ( NSE00000000000000000008 /* OneSignalExtension */, );` to the NSE native target. The `XCRemoteSwiftPackageReference` for `https://github.com/OneSignal/OneSignal-XCFramework` is the one the app target already uses ([ios.md](ios.md) → Dependency); add it to the `PBXProject`'s `packageReferences` first if the project has none.

**CocoaPods:** skip the SPM objects. Add the NSE target block to the `Podfile` with the resolver's `--line-format podfile-nse` output, then run `pod install`:

```ruby
target 'OneSignalNotificationServiceExtension' do
  pod 'OneSignal/OneSignal', 'X.Y.Z' # onesignal:managed v1 — the resolver's exact pin, the same as the app target
end
```

### 7. Check the edit

Run `xcodebuild -list -project <App>.xcodeproj`. A malformed pbxproj fails at once; a good one lists both targets. Then build with `-scmProvider system` ([ios.md](ios.md)): no `No such module 'OneSignalExtension'`, and the built app has `PlugIns/OneSignalNotificationServiceExtension.appex`. Keep code signing on: a build with signing disabled drops `aps-environment` and the App Group from the binary, and the SDK then reports the Push capability as missing.

## Troubleshooting

| Issue | Fix |
|---|---|
| Push arrives, image missing | The NSE is absent or does not run: check the target exists, embeds in the app, links `OneSignalExtension`, and matches the app's deployment target |
| No confirmed delivery | The App Group differs between the targets, the NSE is missing, or the plan does not show it (paid plans only) |
| Badge does not update | The App Groups capability is missing from one target |
| `No such module 'OneSignalExtension'` | The product or pod is not linked to the NSE target |
| "Multiple commands produce Info.plist" | Exclude `Info.plist` through the `PBXFileSystemSynchronizedBuildFileExceptionSet` (step 3) |
| App Group works in the simulator, fails on a device | Select a valid Apple team so Xcode registers the group, or register it in the Apple Developer portal |
