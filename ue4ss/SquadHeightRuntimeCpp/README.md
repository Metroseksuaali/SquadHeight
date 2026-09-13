# SquadHeightRuntimeCpp

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
