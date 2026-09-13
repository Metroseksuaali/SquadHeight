# SquadHeightRuntimeCpp

**English** | [Русский](#русский)

C++ port of the UE4SS `SquadHeightRuntime` (Lua) exporter.

Target environment:

- UE4SS: `v3.0.1 Beta #0`, Git SHA `1c1a1497f942c707f47ba668db75b25e86f6c08a`;
- Unreal Engine: `yobaNGE/UnrealEngine` sources, branch `5.7` (reference only);
- Windows 10 x64;
- the standard UE4SS shipping configuration: `Game__Shipping__Win64`.

## What moved from Lua to C++

The exporter's entire hot path is now in C++:

- F8 handling and start/cancel toggling;
- per-frame budget on the game thread;
- grid traversal;
- calling `UKismetSystemLibrary::LineTraceSingle`;
- parsing `FHitResult`;
- Actor/Component/PCG vegetation/volume/asset-path filtering;
- Actor and Component classification caches;
- re-traces after a filtered hit;
- `topmost` and `terrain_under_overhang`;
- streaming row-by-row `float32 RAW` output;
- normalization, orientation and downsampling;
- writing `heightmap.json`, `heightmap_500.json`, `meta.json`;
- logging.

The Lua VM no longer takes part in scanning, so the Lua GC settings
(`gc_step_kb`, `gc_full_every_cells`) have been removed.

## Why the trace still goes through ProcessEvent

The mod deliberately does **not** link against Unreal Engine sources and does not
call `UWorld::LineTraceSingleByChannel` at a hard-coded address.

The UE 5.7 sources serve as a reference for how the engine works, but retail Squad
does not expose a stable C++ ABI or a set of importable Engine symbols to an ordinary
DLL. A direct call through an AOB/function pointer could be faster, but it would be
tied to one specific exe and would break far more easily after a game update.

In this version the reflected `LineTraceSingle` is located once, and the following
are cached once:

- the UFunction params block size;
- offsets of `WorldContextObject`, `Start`, `End`, `TraceChannel` and the bool parameters;
- the storage type of `FVector.X/Y/Z` (`float`/`double`);
- offsets of `FHitResult.ImpactPoint`, `Location`, normals, `Component`;
- the offset of `HitObjectHandle.ReferenceObject` for UE 5.7.

The hot loop does no repeated FProperty lookups and no Lua marshalling: it writes to
the cached offsets, makes one `ProcessEvent` call, reads the result and looks the
component up in the cache.

## Additional filter optimization

A component is classified only on the first hit against that `UPrimitiveComponent*`.
After a cache hit the mod neither looks up the owning Actor nor walks the Outer chain.
The normal filter stays per-hit, because the normal can vary even within one component.

This matters especially for Landscape/ISM: a single component can be hit millions of times.

## Threading

In this UE4SS build `on_update()` does not run on the Unreal game thread, so no Unreal
objects are called from there.

F8 is polled via Win32 `GetAsyncKeyState` directly from
`RegisterEngineTickPreCallback`. File post-processing after a scan finishes runs on a
separate `std::jthread`; it no longer touches Unreal objects.

### Load diagnostics

After UE4SS starts, these lines should appear:

```text
[SquadHeightRuntimeCpp] Loaded. Persistent EngineTick hook registered; F8 polling starts on the first game tick.
[SquadHeightRuntimeCpp] Persistent EngineTick active. F8 polling is READY.
```

Pressing F8 should immediately log `F8 pressed.`. If it does not, the EngineTick
callback or the Win32 polling is not working; if it does but the export does not start,
the next log line shows the world/map/trace detection error.

## Runtime mapId detection

Since revision 1.0.5 the mod first identifies the active `BP_SQLayer_C` by its `Worlds[]`:
every soft world reference is exported through reflection and compared with the current
`UWorld`'s `GetPathName()`. Only then is `LevelId` read. This mirrors the layer -> world
link used by Squad Pipeline/CUE4Parse instead of guessing from the layer name. If several
`Worlds[]` matches yield different `LevelId`s, the mod refuses to guess. The old
layer/world-name comparison is only a secondary compatibility fallback when `Worlds[]`
produced no match.

Bounds are looked up in this order:

1. exact case-insensitive match of `LevelId` against `Maps.cpp`;
2. if there is no exact match, the part **after the last `_`** is tried again
   (`SD_GooseBay` -> `GooseBay`);
3. if the runtime layer/LevelId gave no map, the previous longest-substring lookup on the
   current `UWorld` name is used;
4. `[ManualMap]` remains an explicit override and takes precedence over automatic detection.

The path taken is logged before `START`, for example:

```text
Map resolver: BP_SQLayer_C 'SD_GooseBay_...' selected via Worlds[] Worlds='/Steel_Division/.../SD_GooseBay_..._d.SD_GooseBay_..._d'; LevelId='SD_GooseBay' -> GooseBay (last-'_' fallback='GooseBay')
```

## Output files

By default:

```text
SquadHeight_output/<Map>/
  heightmap_world_f32.raw
  heightmap.json
  heightmap_500.json
  meta.json
  runtime_export.log
```

`heightmap_world_f32.raw`:

- little-endian IEEE754 `float32`;
- row-major;
- absolute Unreal world Z in meters;
- no-hit = `NaN`.

JSON is normalized as in the original Lua exporter: the minimum valid world Z is
subtracted from all heights, so the minimum becomes `0`.

## Controls

- `F8` while idle: start exporting the current map;
- `F8` during a scan: cancel;
- during file finalization F8 is ignored until `DONE`.

`config.ini` is re-read before every new scan, so settings can be changed without
restarting the game. The hotkey is fixed to F8. It does not depend on the UE4SS Input
Handler: edge detection in EngineTick is used.

For a first check, `resolution_m = 8.0` or `16.0` is recommended. Once the collision
channel and filters are verified, move to `4.0`, then `1.0`.

## Build and install

See `BUILD_WIN10.md`.

Important: the DLL must be built against **the same UE4SS checkout `1c1a1497...`** and in
`Game__Shipping__Win64`. UE4SS C++ mods depend on its ABI/CRT; a plain `Release` build or
a different UE4SS commit cannot be used.

## What was verified in the sources

Verified locally:

- C++23 syntax of the whole project;
- building all translation units into one shared library against an API mock layer;
- `-Wall -Wextra -Wconversion` with no warnings in the mod code;
- the `config.ini` parser;
- longest-match map detection (`Narva_f` does not turn into `Narva`);
- creating and JSON-parsing the full/downsample/meta post-processing results.

The actual MSVC build against your full local UE4SS checkout has to be done on Windows,
since UE4SS is officially built on Windows and its UEPseudo submodule/toolchain were not
available in that environment.

Use runtime modding only in a local/offline environment where it is permitted.
The mod contains no anti-cheat bypass.

## Compatibility revision 1.0.2

For UE4SS commit `1c1a1497`, UEPseudo returns `TObjectPtr<UScriptStruct>` from `FStructProperty::GetStruct()`. Revision 1.0.2 explicitly calls `.Get()` and includes `Unreal/World.hpp` before converting `UWorld*` to `UObject*`. This fixes the MSVC C3535/C2440 cascade reported by the exact `1c1a1497` toolchain.

## Compatibility revision 1.0.3

UE4SS `RegisterEngineTickPreCallback` hook options matter: `{true, true, ...}` is appropriate for one-shot initialization callbacks (as used by EventViewerMod), while UE4SS's persistent Lua game-thread tick uses `{false, false, ...}`. Earlier revisions accidentally used the EventViewer one-shot flags, so `game_tick()` ran only once and later F8 presses could never be observed. Revision 1.0.3 uses `{false, false, ...}` and polls F8 via Win32 on every engine tick.

## Compatibility revision 1.0.4

Map detection now prefers runtime `BP_SQLayer_C.LevelId`. Bounds lookup is exact first; if that
misses, only the suffix after the last underscore is tried (`SD_GooseBay` -> `GooseBay`). If the
runtime layer cannot be selected unambiguously or its id still has no bounds entry, the previous
world-name longest-match resolver remains the fallback.

## Compatibility revision 1.0.5

Active layer selection now uses the reflected `BP_SQLayer_C.Worlds[]` soft references as the primary
source of truth. Each array element is converted with UE's `FProperty::ExportTextItem`, so the code
does not hard-code the runtime layout of `TSoftObjectPtr`/`FSoftObjectPath`; the resulting asset path
is compared with the current `UWorld::GetPathName()`. The layer-name heuristic is only secondary.
`LevelId` bounds lookup remains exact first, then the suffix after the final `_`, then the legacy
world-name detector.

---

<a id="русский"></a>

# SquadHeightRuntimeCpp (Русский)

[English](#squadheightruntimecpp) | **Русский**

C++-порт переданного `SquadHeightRuntime` для UE4SS.

Целевая среда:

- UE4SS: `v3.0.1 Beta #0`, Git SHA `1c1a1497f942c707f47ba668db75b25e86f6c08a`;
- Unreal Engine: исходники `yobaNGE/UnrealEngine`, ветка `5.7`;
- Windows 10 x64;
- штатная shipping-конфигурация UE4SS: `Game__Shipping__Win64`.

## Что перенесено с Lua в C++

В C++ перенесён весь горячий путь экспортёра:

- обработка F8 и переключение start/cancel;
- покадровый budget на game thread;
- обход сетки;
- вызов `UKismetSystemLibrary::LineTraceSingle`;
- разбор `FHitResult`;
- фильтрация Actor/Component/PCG vegetation/volume/asset path;
- кэши классификации Actor и Component;
- повторные трассы после отфильтрованного hit;
- `topmost` и `terrain_under_overhang`;
- потоковая запись `float32 RAW` по строкам;
- нормализация, orientation и downsample;
- генерация `heightmap.json`, `heightmap_500.json`, `meta.json`;
- логирование.

Lua VM в сканировании больше не участвует. Параметры Lua GC (`gc_step_kb`, `gc_full_every_cells`) поэтому удалены.

## Почему трасса всё ещё вызывается через ProcessEvent

Мод намеренно **не** линкуется напрямую с исходниками Unreal Engine и не вызывает
`UWorld::LineTraceSingleByChannel` по жёсткому адресу.

Исходники UE 5.7 нужны как справочник по устройству движка, но сам retail Squad не
предоставляет стабильный C++ ABI/набор импортируемых Engine-symbols для обычной DLL.
Прямой вызов через AOB/function pointer можно сделать быстрее, но он будет привязан к
конкретному exe и намного легче сломается после обновления игры.

В этой версии один раз находится reflected `LineTraceSingle`, после чего один раз
кэшируются:

- размер UFunction params block;
- offsets `WorldContextObject`, `Start`, `End`, `TraceChannel`, bool-параметров;
- тип хранения `FVector.X/Y/Z` (`float`/`double`);
- offsets `FHitResult.ImpactPoint`, `Location`, normals, `Component`;
- offset `HitObjectHandle.ReferenceObject` для UE 5.7.

В горячем цикле нет повторного поиска FProperty и нет Lua-marshalling: выполняется
обычная запись по закэшированным offsets, один `ProcessEvent`, чтение результата и
lookup в кэше компонента.

## Дополнительная оптимизация фильтров

Классификация компонента выполняется только при первом попадании по этому
`UPrimitiveComponent*`. После cache-hit мод не ищет owning Actor и не проходит Outer-chain.
Normal-фильтр остаётся per-hit, потому что нормаль может меняться даже у одного компонента.

Это особенно существенно для Landscape/ISM: один компонент может быть hit-нут миллионы раз.

## Threading

UE4SS `on_update()` в указанной сборке работает не на Unreal game thread, поэтому Unreal
objects оттуда не вызываются.

F8 опрашивается через Win32 `GetAsyncKeyState` непосредственно из
`RegisterEngineTickPreCallback`. Файловая постобработка после окончания scan запускается
отдельным `std::jthread`; она больше не обращается к Unreal objects.


### Диагностика загрузки

После старта UE4SS должны появиться строки:

```text
[SquadHeightRuntimeCpp] Loaded. Persistent EngineTick hook registered; F8 polling starts on the first game tick.
[SquadHeightRuntimeCpp] Persistent EngineTick active. F8 polling is READY.
```

При нажатии F8 должна сразу появиться строка `F8 pressed.`. Если её нет, значит не работает
EngineTick callback или Win32 polling; если она есть, но экспорт не стартует, следующая строка лога
покажет уже ошибку определения world/map/trace.

## Определение mapId в runtime

Начиная с revision 1.0.5 мод сначала определяет активный `BP_SQLayer_C` по его `Worlds[]`:
каждый soft world reference экспортируется через reflection и сравнивается с `GetPathName()`
текущего `UWorld`. Только после этого читается `LevelId`. Это повторяет связь layer -> world,
которую использует Squad Pipeline/CUE4Parse, вместо угадывания по имени layer. Если несколько
`Worlds[]`-совпадений дают разные `LevelId`, мод отказывается угадывать. Старое сравнение
layer/world name используется лишь как вторичный compatibility fallback, если `Worlds[]` не дал
совпадения.

Lookup bounds выполняется в таком порядке:

1. точное case-insensitive совпадение `LevelId` с `Maps.cpp`;
2. если точного совпадения нет, берётся часть **после последнего `_`** и проверяется ещё раз
   (`SD_GooseBay` -> `GooseBay`);
3. если runtime layer/LevelId не дал карту, используется прежний longest-substring lookup по
   имени текущего `UWorld`;
4. `[ManualMap]` остаётся явным override и имеет приоритет над автоматическим определением.

В логе перед `START` печатается использованный путь, например:

```text
Map resolver: BP_SQLayer_C 'SD_GooseBay_...' selected via Worlds[] Worlds='/Steel_Division/.../SD_GooseBay_..._d.SD_GooseBay_..._d'; LevelId='SD_GooseBay' -> GooseBay (last-'_' fallback='GooseBay')
```

## Выходные файлы

По умолчанию:

```text
SquadHeight_output/<Map>/
  heightmap_world_f32.raw
  heightmap.json
  heightmap_500.json
  meta.json
  runtime_export.log
```

`heightmap_world_f32.raw`:

- little-endian IEEE754 `float32`;
- row-major;
- абсолютный Unreal world Z в метрах;
- no-hit = `NaN`.

JSON нормализуется как в исходном Lua-экспортёре: минимальный валидный world Z
вычитается из всех высот, поэтому минимум становится `0`.

## Управление

- `F8` в idle: начать экспорт текущей карты;
- `F8` во время scan: отменить;
- во время файловой финализации F8 игнорируется до `DONE`.

`config.ini` перечитывается перед каждым новым scan, поэтому параметры можно менять без
перезапуска игры. Hotkey фиксирован на F8. Он не зависит от UE4SS Input Handler: используется edge-detection в EngineTick.

Для первой проверки рекомендуется `resolution_m = 8.0` или `16.0`. После проверки
collision channel и фильтров перейти на `4.0`, затем на `1.0`.

## Сборка и установка

См. `BUILD_WIN10.md`.

Важно: собирать DLL нужно против **того же checkout UE4SS `1c1a1497...`** и в
`Game__Shipping__Win64`. C++-моды UE4SS зависят от его ABI/CRT; обычный `Release` или
другой commit UE4SS использовать нельзя.

## Что проверено в исходниках

В комплекте локально проверены:

- C++23 syntax всего проекта;
- сборка всех translation units в одну shared library на API-mock слое;
- `-Wall -Wextra -Wconversion` без предупреждений в коде мода;
- parser `config.ini`;
- longest-match определения карт (`Narva_f` не превращается в `Narva`);
- создание и JSON-парсинг full/downsample/meta результатов постобработки.

Фактическую MSVC-сборку против вашего локального полного checkout UE4SS необходимо
выполнить на Windows, поскольку UE4SS официально собирается на Windows и его UEPseudo
submodule/toolchain отсутствуют в этой среде.

Использовать runtime-моддинг следует только в локальной/offline-среде, где это разрешено.
Мод не содержит обхода античита.

Примечания к ревизиям совместимости 1.0.2–1.0.5 изначально написаны на английском — см.
английскую часть выше.
