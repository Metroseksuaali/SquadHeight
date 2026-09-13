# Архитектура и производительность

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
