# Architecture and performance

**English** | [Русский](#русский)

## Where the Lua version's main overhead was

For every cell the Lua exporter called the reflected `LineTraceSingle` and received the
`FHitResult` out-param back as a Lua structure. On a full 1 m raster this repeats tens of
millions of times. On top of the collision query itself, it pays for:

- the Lua -> UE4SS -> UFunction transition;
- converting Lua vector tables to FVector;
- creating/updating a Lua table for `FHitResult`;
- wrapping UObject/weak pointers;
- string/class/property lookups on cache misses;
- Lua GC.

Optimizing only the Lua tables is not enough: the main savings come from removing Lua
from the per-cell path.

## What runs once

On `on_unreal_init()`, `TraceInvoker` finds the `KismetSystemLibrary` CDO and the
`LineTraceSingle` UFunction, then reads the reflection metadata.

The UFunction parameter layout and the needed parts of `FHitResult` are cached. These are
runtime offsets, not numbers hard-coded for a specific exe.

Before every scan the config is re-read and the filter classes are resolved.

## What runs per cell

1. Compute the XY of the grid point.
2. Fill Start/End in a pre-allocated params block.
3. One `ProcessEvent(LineTraceSingle)`.
4. Read Z, normal and the weak component from the known offsets.
5. Look up `component_cache`.
6. On the first hit against a component, classify the Actor/Component and store the result.
7. Write one `float` into the row buffer.

For excluded geometry a retrace below the hit is done, as in the original Lua version.

## Why FHitResult is not declared by hand

In UE 5.7 `FHitResult` contains, among other things, `FActorInstanceHandle`, weak pointers
and fields whose layout has changed between Unreal versions. So the mod does not duplicate
the C++ struct and does not assume fixed offsets.

`ImpactPoint`, normals, `Component` and `HitObjectHandle.ReferenceObject` are located through
reflection and then accessed through the cached offsets.

## Why not call `UWorld::LineTraceSingleByChannel` directly

It is potentially faster than `ProcessEvent`, but for an injected UE4SS DLL it requires a
stable address/ABI for an internal function of the retail executable. Having the UE 5.7
sources does not by itself create an importable symbol in the game.

An AOB + function pointer approach only makes sense as a separate build-specific backend,
after profiling a specific Squad version. It should not be the default backend of a portable
mod.

## Possible second optimization stage

If profiling after the C++ port shows that the remaining `ProcessEvent` overhead is still
significant, the next options, in priority order:

1. `LineTraceMulti` per column with all hits filtered in C++, to reduce repeated collision
   queries on vegetation/overhangs;
2. a separate direct-native backend with an AOB/signature for a **specific** Squad exe;
3. tile/job scheduling, if the game allows async scene queries to be used safely.

Options 2–3 tie the mod much more tightly to a game version and need separate runtime
validation. The current version intentionally stays on the safe side of this trade-off.

## Runtime map identity (v1.0.5)

Map bounds are no longer selected solely from `UWorld::GetName()`. The scanner first inspects loaded
`BP_SQLayer_C` objects and matches their reflected `Worlds[]` soft references against the current
`UWorld::GetPathName()`. Array elements are read generically through `FArrayProperty` +
`FScriptArrayHelper_InContainer` and converted through `FProperty::ExportTextItem`, avoiding a
hard-coded `FSoftObjectPath`/`TSoftObjectPtr` memory layout. Only the selected layer's reflected
`LevelId` is used for map lookup. If equally strong `Worlds[]` matches disagree on `LevelId`, the
resolver refuses to guess. The older layer-name heuristic is secondary only.

The selected `LevelId` is resolved against `Maps.cpp` with deterministic precedence: exact id first,
then (only if exact misses) the suffix after the final underscore. Thus `SD_GooseBay` can resolve to
`GooseBay`, while an existing exact id containing underscores remains exact. If this path fails, the
legacy longest-substring world-name detector remains available. `[ManualMap]` is still the explicit
override.

---

<a id="русский"></a>

# Архитектура и производительность (Русский)

[English](#architecture-and-performance) | **Русский**

## Где был основной overhead Lua-версии

Для каждой клетки Lua-экспортёр вызывал reflected `LineTraceSingle` и получал out-param
`FHitResult` обратно как Lua-структуру. На полном 1 м raster это повторяется десятки
миллионов раз. Помимо самой collision query оплачиваются:

- переход Lua -> UE4SS -> UFunction;
- преобразование Lua vector tables в FVector;
- создание/обновление Lua table для `FHitResult`;
- оборачивание UObject/weak pointers;
- string/class/property lookup на cache miss;
- Lua GC.

Оптимизировать только Lua-таблицы недостаточно: главная экономия получается при удалении
Lua из per-cell пути.

## Что выполняется один раз

При `on_unreal_init()` `TraceInvoker` находит CDO `KismetSystemLibrary` и UFunction
`LineTraceSingle`, а затем читает reflection metadata.

Кэшируется layout параметров UFunction и нужных частей `FHitResult`. Это runtime offsets,
а не числа, зашитые под конкретный exe.

Перед каждым scan перечитывается config и резолвятся классы фильтров.

## Что выполняется на каждую клетку

1. Вычисление XY точки сетки.
2. Заполнение Start/End в заранее выделенном params block.
3. Один `ProcessEvent(LineTraceSingle)`.
4. Чтение Z, normal и weak component из заранее известных offsets.
5. Lookup `component_cache`.
6. При первом hit по компоненту — классификация Actor/Component и сохранение результата.
7. Запись одного `float` в row buffer.

При исключённой геометрии выполняется retrace ниже hit, как в исходном Lua варианте.

## Почему FHitResult не объявлен вручную

В UE 5.7 `FHitResult` содержит, среди прочего, `FActorInstanceHandle`, weak pointers и
поля, layout которых менялся между версиями Unreal. Поэтому мод не дублирует C++ struct
и не предполагает фиксированные offsets.

`ImpactPoint`, normals, `Component` и `HitObjectHandle.ReferenceObject` находятся через
reflection и затем используются через закэшированные offsets.

## Почему не прямой `UWorld::LineTraceSingleByChannel`

Он потенциально быстрее `ProcessEvent`, но для injected UE4SS DLL это требует стабильного
адреса/ABI внутренней функции retail executable. Наличие исходников UE 5.7 само по себе
не создаёт импортируемый символ в игре.

Вариант с AOB + function pointer разумен только как отдельный build-specific backend после
профилирования конкретной версии Squad. Его не стоит делать default backend переносимого
мода.

## Возможный второй этап оптимизации

Если после C++-порта профилирование покажет, что оставшийся overhead `ProcessEvent` всё ещё
существенен, следующие варианты по приоритету:

1. `LineTraceMulti` на колонку и фильтрация всех hit в C++, чтобы уменьшить число повторных
   collision queries на растительности/overhang;
2. отдельный direct-native backend с AOB/signature для **конкретного** Squad exe;
3. tile/job scheduling, если игра позволяет безопасно использовать async scene queries.

Пункты 2–3 заметно сильнее привязывают мод к версии игры и требуют отдельной runtime
валидации. Текущая версия специально остаётся на безопасной стороне этого компромисса.

Раздел «Runtime map identity (v1.0.5)» изначально написан на английском — см. английскую
часть выше.
