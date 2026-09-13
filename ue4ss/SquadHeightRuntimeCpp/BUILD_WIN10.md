# Building SquadHeightRuntimeCpp on Windows 10

**English** | [Русский](#русский)

> Mod revision: **1.0.5**. F8 is polled directly via Win32 from a persistent EngineTick hook (`{false, false, ...}`); the mapId is resolved first from the runtime `BP_SQLayer_C` (`Worlds[]` -> `LevelId`), then the exact/last-underscore/world-name fallbacks.

## 1. What to install

For UE4SS commit `1c1a1497...` its README requires:

- Windows;
- Visual Studio 2022 version **17.13 or newer**;
- MSVC toolset **14.43+** / compiler **19.43+**;
- the **Desktop development with C++** workload;
- Windows 10/11 SDK;
- CMake **3.22+**;
- Rust toolchain **1.73+**;
- Git;
- Ninja is optional — the Visual Studio generator is used below.

A full UE4SS checkout also needs its `UEPseudo` submodule. Follow UE4SS's requirements
for GitHub/Epic access and SSH if `git submodule update` asks for access.

## 2. Workspace layout

Create, for example:

```text
<workspace>\
  RE-UE4SS\
  SquadHeightRuntimeCpp\
  CMakeLists.txt
```

`<workspace>` is any folder of your choice. The `SquadHeightRuntimeCpp` folder is this
directory (`ue4ss/SquadHeightRuntimeCpp` in the repository).

## 3. Get the exact UE4SS version

In `cmd.exe`:

```bat
cd /d <workspace>
git clone https://github.com/UE4SS-RE/RE-UE4SS.git
git -C RE-UE4SS checkout 1c1a1497f942c707f47ba668db75b25e86f6c08a
git -C RE-UE4SS submodule update --init --recursive
```

Do not add `--remote`: UE4SS itself warns that it may update dependencies to
incompatible revisions.

If you already have a full UE4SS source checkout at exactly this SHA, use it instead of
cloning a second one.

## 4. Create the root CMakeLists.txt

Copy:

```text
SquadHeightRuntimeCpp\CMakeLists.workspace.txt
```

to:

```text
<workspace>\CMakeLists.txt
```

The root file should end up as:

```cmake
cmake_minimum_required(VERSION 3.22)
project(SquadHeightRuntimeWorkspace LANGUAGES C CXX)

add_subdirectory(RE-UE4SS)
add_subdirectory(SquadHeightRuntimeCpp)
```

Do **not** add the `yobaNGE/UnrealEngine` sources to this CMake. The mod uses the UE4SS
C++ API and runtime reflection; the UE 5.7 sources only serve to check structure/semantics.

## 5. Build

The simplest option is to run:

```bat
<workspace>\SquadHeightRuntimeCpp\build_win10.bat
```

The script can be run from any current directory; it resolves the workspace relative to
its own location.

Or manually:

```bat
cd /d <workspace>
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Game__Shipping__Win64 --target SquadHeightRuntimeCpp
```

Do not use the regular Visual Studio `Release` configuration. Retail UE4SS needs:

```text
Game__Shipping__Win64
```

After a successful build CMake prepares:

```text
<workspace>\build\package\Mods\SquadHeightRuntimeCpp\
  config.ini
  dlls\
    main.dll
```

## 6. Install into the game

This assumes UE4SS is already installed and launches with Squad.

Copy the prepared folder into the `Mods` folder your UE4SS installation uses:

```text
Mods\
  SquadHeightRuntimeCpp\
    config.ini
    dlls\
      main.dll
```

In `Mods\mods.txt`, add **above** the built-in `Keybinds`:

```text
SquadHeightRuntimeCpp : 1
```

Disable the old Lua `SquadHeightRuntime` while testing, otherwise both mods may react
to F8.

## 7. First run

Before the first test, in `config.ini`:

```ini
resolution_m = 16.0
```

Then:

1. Start the game in a permitted local/offline environment.
2. Load fully into the map.
3. Press F8 once.
4. Watch the UE4SS console/log and
   `SquadHeight_output\<Map>\runtime_export.log`.
5. Wait for `DONE`.
6. Check `heightmap_500.json`.

If everything is `no-hit`, test `trace_type_query` first at 8–16 m resolution.
Do not run a 1 m scan until the collision channel is confirmed.

If vegetation or an invisible blocker ends up in the map, edit `[Filters]` in
`config.ini`. No recompilation is needed for that.

## 8. Moving to 1 m

Recommended sequence:

```text
16 m -> 8 m -> 4 m -> 1 m
```

On large maps 1 m means tens of millions of traces. C++ greatly reduces the overhead
compared with Lua, but Unreal's physics collision query itself remains the main cost.

For smoother FPS, lower:

```ini
frame_budget_ms = 10.0
```

or `max_cells_per_tick`. For maximum offline export speed you can raise the budget and
accept heavier frame hitches.

## 9. After rebuilding the DLL

Do not try to hot-unload/hot-reload the C++ DLL during a scan/finalization. After
rebuilding `main.dll`, fully restart the game. This guarantees a clean state for the hooks
and the cached reflection pointers.

## 10. Common build errors

### UEPseudo / submodule not found

Check:

```bat
git -C RE-UE4SS submodule status --recursive
```

And retry:

```bat
git -C RE-UE4SS submodule update --init --recursive
```

On an access error, set up the GitHub/Epic/SSH access that UE4SS itself requires.

### CMake does not see `Game__Shipping__Win64`

This is almost always a different UE4SS commit/branch or a broken root workspace.
Check:

```bat
git -C RE-UE4SS rev-parse HEAD
```

Expected:

```text
1c1a1497f942c707f47ba668db75b25e86f6c08a
```

### The DLL builds, but UE4SS does not load it

Check:

```text
Mods\SquadHeightRuntimeCpp\dlls\main.dll
```

and the line in `mods.txt`. Also make sure the DLL was built against **the same** UE4SS SHA
and in `Game__Shipping__Win64`; UE4SS separately warns that C++ mods must match its C
runtime/configuration.

## If submodules use SSH but you do not have a GitHub SSH key

The pinned UE4SS `.gitmodules` uses `git@github.com:` URLs. You can make Git transparently use HTTPS instead:

```bat
git config --global url."https://github.com/".insteadOf git@github.com:
git -C RE-UE4SS submodule sync --recursive
git -C RE-UE4SS submodule update --init --recursive
```

Do not add `--remote`; the pinned submodule commits are part of the required UE4SS ABI/build state.

---

<a id="русский"></a>

# Сборка SquadHeightRuntimeCpp на Windows 10 (Русский)

[English](#building-squadheightruntimecpp-on-windows-10) | **Русский**

> Ревизия мода: **1.0.5**. F8 опрашивается напрямую через Win32 из постоянного EngineTick hook (`{false, false, ...}`); mapId сначала определяется через runtime `BP_SQLayer_C.LevelId`, затем exact/last-underscore/world-name fallback.

## 1. Что установить

Для UE4SS commit `1c1a1497...` его README требует:

- Windows;
- Visual Studio 2022 версии **17.13 или новее**;
- MSVC toolset **14.43+** / compiler **19.43+**;
- workload **Desktop development with C++**;
- Windows 10/11 SDK;
- CMake **3.22+**;
- Rust toolchain **1.73+**;
- Git;
- Ninja необязателен — ниже используется генератор Visual Studio.

Также для полного checkout UE4SS нужен его `UEPseudo` submodule. Следуйте требованиям
UE4SS по GitHub/Epic access и SSH, если `git submodule update` запрашивает доступ.

## 2. Структура рабочей папки

Сделайте, например:

```text
D:\ModDev\SquadHeightWorkspace\
  RE-UE4SS\
  SquadHeightRuntimeCpp\
  CMakeLists.txt
```

Папка `SquadHeightRuntimeCpp` — содержимое этого архива.

## 3. Получить строго нужный UE4SS

В `cmd.exe`:

```bat
cd /d D:\ModDev\SquadHeightWorkspace
git clone https://github.com/UE4SS-RE/RE-UE4SS.git
git -C RE-UE4SS checkout 1c1a1497f942c707f47ba668db75b25e86f6c08a
git -C RE-UE4SS submodule update --init --recursive
```

Не добавляйте `--remote`: UE4SS сам предупреждает, что это может обновить зависимости до
несовместимых ревизий.

Если у вас уже есть полный исходный checkout UE4SS именно этого SHA, используйте его и
не клонируйте второй раз.

## 4. Создать корневой CMakeLists.txt

Скопируйте:

```text
SquadHeightRuntimeCpp\CMakeLists.workspace.txt
```

в:

```text
D:\ModDev\SquadHeightWorkspace\CMakeLists.txt
```

Итоговое содержимое корневого файла:

```cmake
cmake_minimum_required(VERSION 3.22)
project(SquadHeightRuntimeWorkspace LANGUAGES C CXX)

add_subdirectory(RE-UE4SS)
add_subdirectory(SquadHeightRuntimeCpp)
```

Исходники `yobaNGE/UnrealEngine` в этот CMake **добавлять не надо**. Мод использует UE4SS
C++ API и runtime reflection, а исходники UE 5.7 служат для проверки структуры/семантики.

## 5. Собрать

Самый простой вариант — запустить:

```bat
D:\ModDev\SquadHeightWorkspace\SquadHeightRuntimeCpp\build_win10.bat
```

Скрипт можно запускать из любой текущей директории; workspace он вычисляет относительно
собственного расположения.

Или вручную:

```bat
cd /d D:\ModDev\SquadHeightWorkspace
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Game__Shipping__Win64 --target SquadHeightRuntimeCpp
```

Не используйте обычную конфигурацию Visual Studio `Release`. Для retail UE4SS нужна:

```text
Game__Shipping__Win64
```

После успешной сборки CMake подготовит:

```text
D:\ModDev\SquadHeightWorkspace\build\package\Mods\SquadHeightRuntimeCpp\
  config.ini
  dlls\
    main.dll
```

## 6. Установить в игру

Предполагается, что ваш UE4SS уже установлен и запускается с Squad.

Скопируйте готовую папку в тот `Mods`, который использует ваша установка UE4SS:

```text
Mods\
  SquadHeightRuntimeCpp\
    config.ini
    dlls\
      main.dll
```

В `Mods\mods.txt` добавьте **выше** встроенного `Keybinds`:

```text
SquadHeightRuntimeCpp : 1
```

Старый Lua-вариант `SquadHeightRuntime` на время теста отключите, иначе оба мода могут
реагировать на F8.

## 7. Первый запуск

Перед первым тестом в `config.ini`:

```ini
resolution_m = 16.0
```

Затем:

1. Запустить игру в разрешённой локальной/offline-среде.
2. Полностью загрузиться на карту.
3. Нажать F8 один раз.
4. Смотреть UE4SS console/log и
   `SquadHeight_output\<Map>\runtime_export.log`.
5. Дождаться `DONE`.
6. Проверить `heightmap_500.json`.

Если всё `no-hit`, первым делом тестируйте `trace_type_query` на 8–16 м разрешении.
Не запускайте 1 м scan, пока collision channel не подтверждён.

Если в карту попадает растительность/невидимый blocker — правьте `[Filters]` в
`config.ini`. Перекомпиляция для этого не нужна.

## 8. Переход на 1 м

Рекомендуемая последовательность:

```text
16 м -> 8 м -> 4 м -> 1 м
```

На больших картах 1 м означает десятки миллионов трасс. C++ сильно снижает overhead
относительно Lua, но физический collision query Unreal всё равно остаётся основной ценой.

Для более плавного FPS уменьшайте:

```ini
frame_budget_ms = 10.0
```

или `max_cells_per_tick`. Для максимальной скорости офлайн-экспорта можно увеличивать
budget, принимая более сильные frame hitches.

## 9. После пересборки DLL

Не пытайтесь hot-unload/hot-reload C++ DLL во время scan/finalization. После пересборки
`main.dll` полностью перезапустите игру. Это гарантирует чистое состояние hooks и
закэшированных reflection pointers.

## 10. Типичные ошибки сборки

### Не находится UEPseudo / submodule

Проверьте:

```bat
git -C RE-UE4SS submodule status --recursive
```

И повторите:

```bat
git -C RE-UE4SS submodule update --init --recursive
```

При ошибке доступа настройте тот GitHub/Epic/SSH access, который требует сам UE4SS.

### CMake не видит `Game__Shipping__Win64`

Почти всегда используется другой commit/ветка UE4SS или сломан root workspace.
Проверьте:

```bat
git -C RE-UE4SS rev-parse HEAD
```

Ожидается:

```text
1c1a1497f942c707f47ba668db75b25e86f6c08a
```

### DLL собрана, но UE4SS её не грузит

Проверьте:

```text
Mods\SquadHeightRuntimeCpp\dlls\main.dll
```

и строку в `mods.txt`. Также убедитесь, что DLL собрана против **того же** UE4SS SHA и в
`Game__Shipping__Win64`; UE4SS отдельно предупреждает о совпадении C runtime/configuration
для C++-модов.

Раздел о submodules через SSH без GitHub SSH-ключа изначально написан на английском — см.
английскую часть выше.
