#define NOMINMAX
#include "SquadHeightRuntimeMod.hpp"

#include "Maps.hpp"

#include <Windows.h>

#include <algorithm>
#include <cmath>
#include <exception>
#include <ctime>
#include <format>
#include <numbers>
#include <stdexcept>
#include <string_view>
#include <system_error>

#include <DynamicOutput/DynamicOutput.hpp>
#include <Helpers/String.hpp>
#include <Unreal/Hooks/Hooks.hpp>
#include <Unreal/Core/Containers/ScriptArray.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FString.hpp>
#include <Unreal/UEngine.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

using namespace RC;
using namespace RC::Unreal;

namespace SquadHeight
{
namespace
{
constexpr double CmPerMeter = 100.0;
int ModuleAnchor{};

bool matches_instance_base_name(const std::string& low_name, const std::string& low_base)
{
    if (low_name == low_base) return true;
    if (low_name.size() <= low_base.size()) return false;
    if (!std::equal(low_base.begin(), low_base.end(), low_name.begin())) return false;
    return low_name[low_base.size()] == '_';
}

std::string comparison_name(std::string value)
{
    value = lower_ascii(std::move(value));
    // Cooked gameplay worlds are sometimes named <layer>_d while the data
    // asset itself is <layer>.  Treat that terminal marker as non-semantic.
    if (value.size() > 2 && value.ends_with("_d")) value.resize(value.size() - 2);
    return value;
}

std::size_t common_prefix_length(std::string_view a, std::string_view b)
{
    const auto limit = std::min(a.size(), b.size());
    std::size_t count = 0;
    while (count < limit && a[count] == b[count]) ++count;
    return count;
}

std::string compact_identifier(std::string_view value)
{
    std::string out;
    out.reserve(value.size());
    for (const char ch : value)
    {
        const auto c = static_cast<unsigned char>(ch);
        if (c >= 'A' && c <= 'Z') out.push_back(static_cast<char>(c - 'A' + 'a'));
        else if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) out.push_back(static_cast<char>(c));
    }
    return out;
}

std::string extract_unreal_object_path(std::string_view text)
{
    // ExportTextItem for a soft reference can be either a bare path or e.g.
    // World'/Game/Maps/Foo.Foo'.  Starting at the first slash handles both,
    // and also handles FSoftObjectPath's struct-style exported form.
    const auto start = text.find('/');
    if (start == std::string_view::npos) return {};

    auto end = start;
    while (end < text.size())
    {
        const char ch = text[end];
        if (ch == '\'' || ch == '"' || ch == ')' || ch == ',' ||
            ch == '\r' || ch == '\n' || ch == '\t' || ch == ' ')
            break;
        ++end;
    }
    return std::string(text.substr(start, end - start));
}

struct ParsedObjectPath
{
    std::string full;
    std::string package;
    std::string asset;
};

void strip_pie_prefix(std::string& token)
{
    const auto low = lower_ascii(token);
    constexpr std::string_view prefix = "uedpie_";
    if (!low.starts_with(prefix)) return;

    auto pos = prefix.size();
    while (pos < token.size() && token[pos] >= '0' && token[pos] <= '9') ++pos;
    if (pos < token.size() && token[pos] == '_') token.erase(0, pos + 1);
}

ParsedObjectPath parse_object_path(std::string value)
{
    value = extract_unreal_object_path(value);
    if (value.empty()) return {};

    // FSoftObjectPath subobjects are irrelevant for selecting the UWorld.
    if (const auto colon = value.find(':'); colon != std::string::npos) value.resize(colon);

    std::replace(value.begin(), value.end(), '\\', '/');
    value = lower_ascii(std::move(value));

    ParsedObjectPath result{};
    const auto slash = value.find_last_of('/');
    const auto dot = value.find_last_of('.');
    if (dot != std::string::npos && (slash == std::string::npos || dot > slash))
    {
        result.package = value.substr(0, dot);
        result.asset = value.substr(dot + 1);
    }
    else
    {
        result.package = value;
    }

    // PIE changes the package/asset leaf to UEDPIE_<n>_<name>. Strip only
    // that synthetic prefix; do not otherwise rewrite the asset identity.
    auto package_slash = result.package.find_last_of('/');
    std::string package_leaf = package_slash == std::string::npos
                                   ? result.package
                                   : result.package.substr(package_slash + 1);
    strip_pie_prefix(package_leaf);
    if (package_slash == std::string::npos)
        result.package = package_leaf;
    else
        result.package = result.package.substr(0, package_slash + 1) + package_leaf;

    strip_pie_prefix(result.asset);
    result.full = result.package;
    if (!result.asset.empty()) result.full += "." + result.asset;
    return result;
}

std::string path_leaf(std::string_view path)
{
    const auto slash = path.find_last_of('/');
    return std::string(slash == std::string_view::npos ? path : path.substr(slash + 1));
}

int soft_world_match_score(std::string_view soft_world_path, std::string_view current_world_path)
{
    const auto soft = parse_object_path(std::string(soft_world_path));
    const auto current = parse_object_path(std::string(current_world_path));
    if (soft.package.empty() || current.package.empty()) return 0;

    if (!soft.asset.empty() && !current.asset.empty() && soft.full == current.full) return 120000;
    if (soft.package == current.package)
    {
        if (soft.asset.empty() || current.asset.empty() || soft.asset == current.asset) return 115000;
    }

    // Some cooked layer references and runtime worlds only differ by the
    // conventional terminal _d marker. This is weaker than an exact path,
    // but still requires the same package hierarchy.
    const auto soft_pkg_leaf = comparison_name(path_leaf(soft.package));
    const auto current_pkg_leaf = comparison_name(path_leaf(current.package));
    const auto soft_asset = comparison_name(soft.asset);
    const auto current_asset = comparison_name(current.asset);

    const auto soft_pkg_slash = soft.package.find_last_of('/');
    const auto current_pkg_slash = current.package.find_last_of('/');
    const auto soft_parent = soft_pkg_slash == std::string::npos ? std::string{} : soft.package.substr(0, soft_pkg_slash + 1);
    const auto current_parent = current_pkg_slash == std::string::npos ? std::string{} : current.package.substr(0, current_pkg_slash + 1);

    if (soft_parent == current_parent && soft_pkg_leaf == current_pkg_leaf)
    {
        if (soft.asset.empty() || current.asset.empty() || soft_asset == current_asset) return 110000;
    }

    // Do not fall back to asset-name-only matching here: two content sources
    // can legally contain identically named worlds. If package identity does
    // not agree, this is not strong enough evidence to select a runtime layer.
    return 0;
}

std::vector<std::string> read_layer_world_paths(UObject* object)
{
    std::vector<std::string> paths;
    if (!object) return paths;

    auto* worlds_property = CastField<FArrayProperty>(object->GetPropertyByNameInChain(STR("Worlds")));
    if (!worlds_property) return paths;

    auto* inner_property = worlds_property->GetInner();
    if (!inner_property) return paths;

    FScriptArrayHelper_InContainer array_helper(worlds_property, object);
    const int32 count = array_helper.Num();
    if (count > 0) paths.reserve(static_cast<std::size_t>(count));
    for (int32 index = 0; index < count; ++index)
    {
        FString exported{};
        inner_property->ExportTextItem(exported, array_helper.GetRawPtr(index), nullptr, object, 0);
        const auto path = extract_unreal_object_path(RC::to_string(*exported));
        if (path.empty()) continue;
        if (std::none_of(paths.begin(), paths.end(), [&](const auto& existing) {
                return lower_ascii(existing) == lower_ascii(path);
            }))
            paths.push_back(path);
    }
    return paths;
}

int layer_world_score(std::string_view layer_name, std::string_view level_id, std::string_view world_name)
{
    const auto layer = comparison_name(std::string(layer_name));
    const auto world = comparison_name(std::string(world_name));
    if (layer.empty() || world.empty()) return 0;

    if (layer == world) return 100000;
    if (world.find(layer) != std::string::npos || layer.find(world) != std::string::npos)
        return 80000 + static_cast<int>(std::min(layer.size(), world.size()));

    const auto compact_layer = compact_identifier(layer);
    const auto compact_world = compact_identifier(world);
    if (!compact_layer.empty() && compact_layer == compact_world) return 95000;
    if (!compact_layer.empty() && !compact_world.empty() &&
        (compact_world.find(compact_layer) != std::string::npos || compact_layer.find(compact_world) != std::string::npos))
        return 70000 + static_cast<int>(std::min(compact_layer.size(), compact_world.size()));

    int score = 0;
    const auto prefix = common_prefix_length(layer, world);
    // Ignore weak common prefixes such as "sd_".
    if (prefix >= 6) score += static_cast<int>(prefix * 100);

    const auto map_lookup = find_map_id(level_id);
    if (map_lookup.map)
    {
        const auto canonical = lower_ascii(map_lookup.map->name);
        const auto compact_canonical = compact_identifier(canonical);
        if ((!canonical.empty() && world.find(canonical) != std::string::npos) ||
            (!compact_canonical.empty() && compact_world.find(compact_canonical) != std::string::npos))
            score += 20000 + static_cast<int>(canonical.size());
    }
    return score;
}
} // namespace

SquadHeightRuntimeMod::SquadHeightRuntimeMod()
{
    ModName = STR("SquadHeightRuntimeCpp");
    ModVersion = STR("1.0.5");
    ModDescription = STR("Runtime Squad heightmap exporter; C++ port of SquadHeightRuntime Lua");
    ModAuthors = STR("SquadHeightRuntime C++ port");

    // F8 is intentionally polled from the game-thread EngineTick callback instead
    // of UE4SS' input handler. This avoids input-source/focus/callback-lifetime
    // differences between UE4SS builds and keeps the entire toggle path on the
    // game thread. See game_tick().
}

SquadHeightRuntimeMod::~SquadHeightRuntimeMod()
{
    m_cancel_requested.store(true, std::memory_order_release);
    if (m_raw_file.is_open()) m_raw_file.close();
    if (m_finalizer.joinable())
    {
        m_finalizer.request_stop();
        m_finalizer.join();
    }
    close_log();
}

auto SquadHeightRuntimeMod::on_unreal_init() -> void
{
    m_mod_directory = module_directory();

    std::vector<std::string> warnings;
    m_config = Config::load(m_mod_directory / "config.ini", warnings);
    for (const auto& warning : warnings) log("WARNING: " + warning);

    std::string error;
    m_trace_ready = m_tracer.initialize(error);
    if (!m_trace_ready)
    {
        log("ERROR: trace reflection initialization failed: " + error);
    }

    Unreal::Hook::RegisterEngineTickPreCallback(
        [this](auto&, Unreal::UEngine*, float, bool) {
            game_tick();
        },
        {false, false, STR("SquadHeightRuntimeCpp"), STR("ScanTick")});

    log("Loaded. Persistent EngineTick hook registered; F8 polling starts on the first game tick.");
    log("Mod directory: " + RC::to_string(m_mod_directory.wstring()));
    log(std::string("Trace reflection: ") + (m_trace_ready ? "READY" : "NOT READY"));
}

void SquadHeightRuntimeMod::game_tick()
{
    if (!m_tick_seen)
    {
        m_tick_seen = true;
        log("Persistent EngineTick active. F8 polling is READY.");
    }

    // Use the high bit (currently down), then detect only the up->down edge.
    // This prevents key repeat from repeatedly toggling the exporter while F8 is held.
    const bool f8_down = (GetAsyncKeyState(VK_F8) & 0x8000) != 0;
    if (f8_down && !m_f8_was_down)
    {
        log("F8 pressed.");
        m_toggle_requested.store(true, std::memory_order_release);
    }
    m_f8_was_down = f8_down;

    if (m_toggle_requested.exchange(false, std::memory_order_acq_rel)) handle_toggle();

    if (m_phase.load(std::memory_order_acquire) == Phase::Scanning)
    {
        if (m_cancel_requested.load(std::memory_order_acquire))
        {
            cancel_scan("Export cancelled by F8.");
            return;
        }
        process_chunk();
    }
}

void SquadHeightRuntimeMod::handle_toggle()
{
    switch (m_phase.load(std::memory_order_acquire))
    {
    case Phase::Idle:
        start_scan();
        break;
    case Phase::Scanning:
        m_cancel_requested.store(true, std::memory_order_release);
        log("Cancellation requested; stopping on this game tick.");
        break;
    case Phase::Finalizing:
        log("Exporter is finalizing files; F8 is ignored until DONE.");
        break;
    }
}

std::filesystem::path SquadHeightRuntimeMod::module_directory() const
{
    HMODULE module{};
    if (!GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            reinterpret_cast<LPCWSTR>(&ModuleAnchor),
            &module))
    {
        return std::filesystem::current_path();
    }

    std::wstring buffer(32768, L'\0');
    const DWORD length = GetModuleFileNameW(module, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) return std::filesystem::current_path();
    buffer.resize(length);

    auto dir = std::filesystem::path(buffer).parent_path();
    auto leaf = lower_ascii(RC::to_string(dir.filename().wstring()));
    if (leaf == "dlls") dir = dir.parent_path();
    return dir;
}

std::string SquadHeightRuntimeMod::safe_path_component(std::string value)
{
    constexpr std::string_view invalid = "<>:\"/\\|?*";
    for (char& c : value)
    {
        if (invalid.find(c) != std::string_view::npos || static_cast<unsigned char>(c) < 0x20) c = '_';
    }
    while (!value.empty() && (value.back() == '.' || value.back() == ' ')) value.pop_back();
    return value.empty() ? "UnknownMap" : value;
}

std::string SquadHeightRuntimeMod::normalize_world_name(std::string value)
{
    if (value.rfind("UEDPIE_", 0) == 0)
    {
        const auto pos = value.find('_', 7);
        if (pos != std::string::npos) value.erase(0, pos + 1);
    }
    return value;
}

std::string SquadHeightRuntimeMod::object_name(UObject* object)
{
    return object ? RC::to_string(object->GetName()) : std::string{};
}

std::string SquadHeightRuntimeMod::class_name(UObject* object)
{
    return object && object->GetClassPrivate() ? RC::to_string(object->GetClassPrivate()->GetName()) : std::string{};
}

UClass* SquadHeightRuntimeMod::find_class(const std::string& path) const
{
    const auto wide = RC::to_wstring(path);
    return UObjectGlobals::StaticFindObject<UClass*>(nullptr, nullptr, wide.c_str());
}

void SquadHeightRuntimeMod::resolve_filter_classes()
{
    m_actor_class = find_class("/Script/Engine.Actor");
    m_static_mesh_component_class = find_class("/Script/Engine.StaticMeshComponent");
    m_landscape_proxy_class = find_class("/Script/Landscape.LandscapeProxy");

    m_excluded_base_classes.clear();
    for (const auto& path : m_config.exclude_actor_base_classes)
    {
        if (auto* cls = find_class(path)) m_excluded_base_classes.emplace_back(cls);
        else log("WARNING: exclude base class not loaded/found: " + path);
    }

    m_excluded_actor_classes.clear();
    for (const auto& name : m_config.exclude_actor_classes) m_excluded_actor_classes.insert(lower_ascii(name));
    m_excluded_component_classes.clear();
    for (const auto& name : m_config.exclude_component_classes) m_excluded_component_classes.insert(lower_ascii(name));
    m_volume_component_classes.clear();
    for (const auto& name : m_config.volume_shape_component_classes) m_volume_component_classes.insert(lower_ascii(name));

    auto lower_vector = [](const std::vector<std::string>& source) {
        std::vector<std::string> out;
        out.reserve(source.size());
        for (const auto& item : source) out.emplace_back(lower_ascii(item));
        return out;
    };
    m_actor_class_prefixes_lower = lower_vector(m_config.exclude_actor_class_prefixes);
    m_pcg_actor_prefixes_lower = lower_vector(m_config.pcg_vegetation_actor_name_prefixes);
    m_pcg_component_bases_lower = lower_vector(m_config.pcg_vegetation_component_name_bases);
    m_asset_keywords_lower = lower_vector(m_config.exclude_asset_path_keywords);
}

bool SquadHeightRuntimeMod::class_is_actor(UObject* object) const
{
    return object && m_actor_class && object->GetClassPrivate() && object->GetClassPrivate()->IsChildOf(m_actor_class);
}

std::optional<SquadHeightRuntimeMod::RuntimeLayerIdentity> SquadHeightRuntimeMod::resolve_runtime_layer_identity()
{
    std::vector<UObject*> objects;
    UObjectGlobals::FindAllOf(STR("BP_SQLayer_C"), objects);

    struct Candidate
    {
        RuntimeLayerIdentity identity;
        int worlds_score{};
        int name_score{};
    };

    const auto current_world_path = m_world ? RC::to_string(m_world->GetPathName()) : std::string{};
    std::vector<Candidate> candidates;
    candidates.reserve(objects.size());

    for (auto* object : objects)
    {
        if (!object) continue;
        const auto layer_name = object_name(object);
        const auto low_name = lower_ascii(layer_name);
        if (low_name.empty() || low_name.rfind("default__", 0) == 0 ||
            low_name.rfind("skel_", 0) == 0 || low_name.rfind("reinst_", 0) == 0)
            continue;

        try
        {
            auto* level_id_name = object->GetValuePtrByPropertyNameInChain<FName>(STR("LevelId"));
            if (!level_id_name) continue;
            const auto level_id = RC::to_string(level_id_name->ToString());
            if (level_id.empty() || lower_ascii(level_id) == "none") continue;

            Candidate candidate{};
            candidate.identity.layer_name = layer_name;
            candidate.identity.level_id = level_id;
            candidate.name_score = layer_world_score(layer_name, level_id, m_world_name);

            for (const auto& world_path : read_layer_world_paths(object))
            {
                const int score = soft_world_match_score(world_path, current_world_path);
                if (score > candidate.worlds_score)
                {
                    candidate.worlds_score = score;
                    candidate.identity.matched_world_path = world_path;
                }
            }

            const bool duplicate = std::any_of(candidates.begin(), candidates.end(), [&](const auto& existing) {
                return lower_ascii(existing.identity.layer_name) == low_name &&
                       lower_ascii(existing.identity.level_id) == lower_ascii(level_id) &&
                       lower_ascii(existing.identity.matched_world_path) == lower_ascii(candidate.identity.matched_world_path);
            });
            if (!duplicate) candidates.push_back(std::move(candidate));
        }
        catch (...)
        {
            // A malformed/stale UObject must not make map detection fatal.
        }
    }

    if (candidates.empty()) return std::nullopt;

    auto select_same_level_id = [&](const std::vector<std::size_t>& indexes, std::string_view method)
        -> std::optional<RuntimeLayerIdentity> {
        if (indexes.empty()) return std::nullopt;

        const auto key = lower_ascii(candidates[indexes.front()].identity.level_id);
        for (const auto index : indexes)
        {
            if (lower_ascii(candidates[index].identity.level_id) != key) return std::nullopt;
        }

        auto selected = candidates[indexes.front()].identity;
        selected.match_method = std::string(method);
        return selected;
    };

    // Primary selector: the layer explicitly references the currently loaded
    // UWorld through Worlds[]. This mirrors the relation used by the CUE4Parse
    // pipeline instead of inferring the layer from its asset/object name.
    int best_worlds_score = 0;
    std::vector<std::size_t> best_worlds_indexes;
    for (std::size_t index = 0; index < candidates.size(); ++index)
    {
        const int score = candidates[index].worlds_score;
        if (score <= 0) continue;
        if (score > best_worlds_score)
        {
            best_worlds_score = score;
            best_worlds_indexes.assign(1, index);
        }
        else if (score == best_worlds_score)
        {
            best_worlds_indexes.push_back(index);
        }
    }

    if (!best_worlds_indexes.empty())
    {
        if (auto selected = select_same_level_id(best_worlds_indexes, "Worlds[]")) return selected;

        std::string ids;
        for (const auto index : best_worlds_indexes)
        {
            const auto& id = candidates[index].identity.level_id;
            if (!ids.empty()) ids += ", ";
            if (ids.find(id) == std::string::npos) ids += id;
        }
        log("WARNING: BP_SQLayer_C Worlds[] matched current UWorld ambiguously across different LevelId values: " +
            ids + ". Refusing to guess; using world-name fallback.");
        return std::nullopt;
    }

    // Secondary compatibility selector for unusual builds where Worlds[] is
    // not present/loaded in the runtime object. It never overrides a Worlds[]
    // match and it must be unambiguous by LevelId.
    int best_name_score = 0;
    std::vector<std::size_t> best_name_indexes;
    for (std::size_t index = 0; index < candidates.size(); ++index)
    {
        const int score = candidates[index].name_score;
        if (score <= 0) continue;
        if (score > best_name_score)
        {
            best_name_score = score;
            best_name_indexes.assign(1, index);
        }
        else if (score == best_name_score)
        {
            best_name_indexes.push_back(index);
        }
    }

    if (auto selected = select_same_level_id(best_name_indexes, "name heuristic")) return selected;
    return std::nullopt;
}

void SquadHeightRuntimeMod::setup_output_paths()
{
    auto root = m_config.output_root;
    if (root.is_relative()) root = std::filesystem::current_path() / root;

    const auto output_name = safe_path_component(m_config.output_name.empty() ? m_map_name : m_config.output_name);
    m_output_dir = root / RC::to_wstring(output_name);
    std::filesystem::create_directories(m_output_dir);

    m_raw_path = m_output_dir / L"heightmap_world_f32.raw";
    m_full_json_path = m_output_dir / L"heightmap.json";
    m_meta_path = m_output_dir / L"meta.json";
    m_log_path = m_output_dir / L"runtime_export.log";
    if (m_config.downsample_to > 0)
    {
        m_downsample_path = m_output_dir / RC::to_wstring("heightmap_" + std::to_string(m_config.downsample_to) + ".json");
    }
    else
    {
        m_downsample_path.clear();
    }
}

void SquadHeightRuntimeMod::start_scan()
{
    if (!m_trace_ready)
    {
        log("ERROR: cannot start: LineTraceSingle reflection was not initialized.");
        return;
    }

    // Reload config on every new F8 run so tuning does not require restarting the game.
    std::vector<std::string> warnings;
    m_config = Config::load(m_mod_directory / "config.ini", warnings);
    for (const auto& warning : warnings) log("WARNING: " + warning);

    m_cancel_requested.store(false, std::memory_order_release);
    m_actor_cache.clear();
    m_component_cache.clear();
    m_actor_cache.reserve(4096);
    m_component_cache.reserve(16384);

    resolve_filter_classes();
    if (!m_actor_class)
    {
        log("ERROR: /Script/Engine.Actor was not found.");
        return;
    }

    UObject* player_controller = nullptr;
    std::vector<UObject*> controllers;
    UObjectGlobals::FindAllOf(STR("PlayerController"), controllers);
    for (auto it = controllers.rbegin(); it != controllers.rend(); ++it)
    {
        if (*it && (*it)->GetWorld())
        {
            player_controller = *it;
            break;
        }
    }

    if (player_controller)
    {
        if (auto** pawn = player_controller->GetValuePtrByPropertyNameInChain<UObject*>(STR("Pawn")); pawn && *pawn)
            m_world_context = *pawn;
        else
            m_world_context = player_controller;
        m_world = static_cast<UObject*>(player_controller->GetWorld());
    }
    else
    {
        auto* game_instance = UObjectGlobals::FindFirstOf(STR("GameInstance"));
        if (game_instance)
        {
            m_world = static_cast<UObject*>(game_instance->GetWorld());
            m_world_context = m_world ? m_world : game_instance;
        }
    }

    if (!m_world || !m_world_context)
    {
        log("ERROR: UWorld is not available. Load fully into a map first.");
        return;
    }

    m_world_name = normalize_world_name(object_name(m_world));
    std::string map_resolution_log;
    if (m_config.manual_bounds_enabled)
    {
        m_map_name = m_config.manual_map_name;
        m_bounds = m_config.manual_bounds;
        m_rotation_deg = m_config.grid_rotation_deg;
        map_resolution_log = "Map resolver: ManualMap override -> " + m_map_name;
    }
    else
    {
        const MapDef* map = nullptr;
        std::optional<RuntimeLayerIdentity> runtime_identity = resolve_runtime_layer_identity();
        if (runtime_identity)
        {
            const auto lookup = find_map_id(runtime_identity->level_id);
            map = lookup.map;

            map_resolution_log = "Map resolver: BP_SQLayer_C '" + runtime_identity->layer_name +
                                 "' selected via " + runtime_identity->match_method;
            if (!runtime_identity->matched_world_path.empty())
                map_resolution_log += " Worlds='" + runtime_identity->matched_world_path + "'";
            map_resolution_log += "; LevelId='" + runtime_identity->level_id + "'";

            if (map)
            {
                map_resolution_log += " -> " + std::string(map->name);
                if (lookup.used_last_underscore_suffix)
                    map_resolution_log += " (last-'_' fallback='" + lookup.lookup_id + "')";
                else
                    map_resolution_log += " (exact)";
            }
            else
            {
                map_resolution_log +=
                    " has no exact/last-'_' bounds match; using world-name fallback.";
            }
        }

        if (!map)
        {
            map = detect_map(m_world_name);
            if (map)
            {
                if (!map_resolution_log.empty()) map_resolution_log += " ";
                map_resolution_log += "World-name fallback '" + m_world_name + "' -> " + map->name;
            }
        }

        if (!map)
        {
            std::string detail;
            if (runtime_identity) detail = " Runtime LevelId='" + runtime_identity->level_id + "'.";
            log("ERROR: unknown map/world '" + m_world_name + "'." + detail +
                " Set [ManualMap] in config.ini or add bounds in Maps.cpp.");
            return;
        }

        m_map_name = map->name;
        m_bounds = map->bounds;
        m_rotation_deg = map->grid_rotation_deg.value_or(m_config.grid_rotation_deg);
    }

    try
    {
        setup_output_paths();
    }
    catch (const std::exception& e)
    {
        log(std::string("ERROR: creating output directory failed: ") + e.what());
        return;
    }

    {
        std::scoped_lock lock(m_log_mutex);
        if (m_log_file.is_open()) m_log_file.close();
        m_log_file.open(m_log_path, std::ios::out | std::ios::app);
    }

    m_raw_file.open(m_raw_path, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!m_raw_file)
    {
        abort_scan("Could not open raw output: " + m_raw_path.string());
        return;
    }

    std::string trace_error;
    if (!m_tracer.configure(m_world_context, m_config.trace_type_query, trace_error))
    {
        abort_scan("Trace configuration failed: " + trace_error);
        return;
    }

    m_step_cm = m_config.resolution_m * CmPerMeter;
    const double min_x = m_bounds.min_x * CmPerMeter;
    const double max_x = m_bounds.max_x * CmPerMeter;
    const double min_y = m_bounds.min_y * CmPerMeter;
    const double max_y = m_bounds.max_y * CmPerMeter;

    m_cols = static_cast<std::size_t>(std::floor((max_x - min_x) / m_step_cm)) + 1;
    m_rows = static_cast<std::size_t>(std::floor((max_y - min_y) / m_step_cm)) + 1;
    m_stats = {};
    m_stats.total_cells = static_cast<std::uint64_t>(m_rows) * static_cast<std::uint64_t>(m_cols);

    m_center_x = (min_x + max_x) * 0.5;
    m_center_y = (min_y + max_y) * 0.5;
    m_half_u = static_cast<double>(m_cols - 1) * m_step_cm * 0.5;
    m_half_v = static_cast<double>(m_rows - 1) * m_step_cm * 0.5;

    const double radians = m_rotation_deg * std::numbers::pi_v<double> / 180.0;
    m_cos_r = std::cos(radians);
    m_sin_r = std::sin(radians);
    m_z_top_cm = m_config.trace_top_m * CmPerMeter;
    m_z_bottom_cm = m_config.trace_bottom_m * CmPerMeter;

    m_row = 0;
    m_col = 0;
    m_cells_done = 0;
    m_row_buffer.assign(m_cols, std::numeric_limits<float>::quiet_NaN());
    m_world_z_min_m = std::numeric_limits<double>::infinity();
    m_world_z_max_m = -std::numeric_limits<double>::infinity();
    m_scan_started = std::chrono::steady_clock::now();
    m_last_progress = m_scan_started;

    m_phase.store(Phase::Scanning, std::memory_order_release);

    if (!map_resolution_log.empty()) log(map_resolution_log);
    log(std::format("START map={} world={} grid={}x{} @ {:.2f}m ({} cells)",
                    m_map_name, m_world_name, m_cols, m_rows, m_config.resolution_m, m_stats.total_cells));
    log(std::format("Bounds X {:.1f}..{:.1f} m | Y {:.1f}..{:.1f} m | rotation {:.2f} deg",
                    m_bounds.min_x, m_bounds.max_x, m_bounds.min_y, m_bounds.max_y, m_rotation_deg));
    log(std::format("Trace query={} complex={} Z {:.0f}..{:.0f} m mode={} | budget={} cells / {:.1f} ms",
                    m_config.trace_type_query, m_config.trace_complex, m_config.trace_bottom_m, m_config.trace_top_m,
                    m_config.surface_mode, m_config.max_cells_per_tick, m_config.frame_budget_ms));
}

bool SquadHeightRuntimeMod::actor_is_excluded(UObject* actor) const
{
    if (!actor || !actor->GetClassPrivate()) return false;
    const auto low_class = lower_ascii(class_name(actor));
    if (m_excluded_actor_classes.contains(low_class)) return true;

    for (auto* base : m_excluded_base_classes)
    {
        if (base && actor->GetClassPrivate()->IsChildOf(base)) return true;
    }
    for (const auto& prefix : m_actor_class_prefixes_lower)
    {
        if (low_class.rfind(prefix, 0) == 0) return true;
    }
    return false;
}

bool SquadHeightRuntimeMod::actor_is_landscape(UObject* actor) const
{
    if (!actor || !actor->GetClassPrivate()) return false;
    if (m_landscape_proxy_class && actor->GetClassPrivate()->IsChildOf(m_landscape_proxy_class)) return true;
    const auto name = lower_ascii(class_name(actor));
    return name == "landscape" || name == "landscapeproxy" || name == "landscapestreamingproxy";
}

bool SquadHeightRuntimeMod::actor_has_static_mesh(UObject* actor, ActorInfo& info)
{
    if (info.has_static_mesh_known) return info.has_static_mesh;
    info.has_static_mesh_known = true;
    info.has_static_mesh = false;

    if (!actor || !class_is_actor(actor)) return false;
    if (!m_static_mesh_component_class)
    {
        info.has_static_mesh = !contains_ci(class_name(actor), "volume");
        return info.has_static_mesh;
    }

    try
    {
        const auto& components = static_cast<AActor*>(actor)->K2_GetComponentsByClass(m_static_mesh_component_class);
        info.has_static_mesh = components.Num() > 0;
    }
    catch (...)
    {
        info.has_static_mesh = !contains_ci(class_name(actor), "volume");
    }
    return info.has_static_mesh;
}

SquadHeightRuntimeMod::ActorInfo& SquadHeightRuntimeMod::actor_info(UObject* actor)
{
    auto [it, inserted] = m_actor_cache.try_emplace(actor);
    auto& info = it->second;
    if (inserted)
    {
        info.excluded = actor_is_excluded(actor);
        info.landscape = !info.excluded && actor_is_landscape(actor);
    }
    return info;
}

std::string SquadHeightRuntimeMod::component_mesh_path(UObject* component) const
{
    if (!component) return {};
    try
    {
        auto** mesh = component->GetValuePtrByPropertyNameInChain<UObject*>(STR("StaticMesh"));
        if (mesh && *mesh) return RC::to_string((*mesh)->GetFullName());
    }
    catch (...)
    {
    }
    return {};
}

bool SquadHeightRuntimeMod::is_known_pcg_vegetation(UObject* actor, UObject* component) const
{
    if (!component || m_pcg_component_bases_lower.empty()) return false;
    const auto low_component_name = lower_ascii(object_name(component));
    bool component_matches = false;
    for (const auto& base : m_pcg_component_bases_lower)
    {
        if (matches_instance_base_name(low_component_name, base))
        {
            component_matches = true;
            break;
        }
    }
    if (!component_matches) return false;
    if (!m_config.pcg_vegetation_require_actor_prefix) return true;
    if (!actor) return false;

    const auto low_actor_name = lower_ascii(object_name(actor));
    for (const auto& prefix : m_pcg_actor_prefixes_lower)
    {
        if (low_actor_name.rfind(prefix, 0) == 0) return true;
    }
    return false;
}

bool SquadHeightRuntimeMod::component_is_excluded(UObject* actor, UObject* component)
{
    if (!component) return false;
    const auto low_class = lower_ascii(class_name(component));
    if (m_excluded_component_classes.contains(low_class)) return true;

    if (is_known_pcg_vegetation(actor, component))
    {
        ++m_stats.vegetation_components_filtered;
        return true;
    }

    if (m_volume_component_classes.contains(low_class))
    {
        if (!actor) return true;
        auto& info = actor_info(actor);
        if (!actor_has_static_mesh(actor, info)) return true;
    }

    if (!m_asset_keywords_lower.empty())
    {
        const auto low_path = lower_ascii(component_mesh_path(component));
        if (!low_path.empty())
        {
            for (const auto& keyword : m_asset_keywords_lower)
            {
                if (low_path.find(keyword) != std::string::npos) return true;
            }
        }
    }
    return false;
}

UObject* SquadHeightRuntimeMod::resolve_actor(const TraceHit& hit) const
{
    if (class_is_actor(hit.reference_object)) return hit.reference_object;

    for (UObject* object = hit.component; object; object = object->GetOuterPrivate())
    {
        if (class_is_actor(object)) return object;
    }
    for (UObject* object = hit.reference_object; object; object = object->GetOuterPrivate())
    {
        if (class_is_actor(object)) return object;
    }
    return nullptr;
}

SquadHeightRuntimeMod::ComponentInfo SquadHeightRuntimeMod::classify_hit(const TraceHit& hit)
{
    ComponentInfo base{};

    // This branch is the hot path. Once a component has been classified, avoid
    // resolving the owning actor or walking Outer chains again. On a heightmap
    // scan the same landscape/ISM component can be hit millions of times.
    if (hit.component)
    {
        if (const auto it = m_component_cache.find(hit.component); it != m_component_cache.end())
        {
            base = it->second;
        }
        else
        {
            UObject* actor = resolve_actor(hit);
            if (actor)
            {
                auto& ai = actor_info(actor);
                base.excluded = ai.excluded;
                base.landscape = !base.excluded && ai.landscape;
            }
            if (!base.excluded && component_is_excluded(actor, hit.component)) base.excluded = true;
            if (base.excluded) base.landscape = false;
            m_component_cache.emplace(hit.component, base);
        }
    }
    else
    {
        UObject* actor = resolve_actor(hit);
        if (actor)
        {
            auto& ai = actor_info(actor);
            base.excluded = ai.excluded;
            base.landscape = !base.excluded && ai.landscape;
        }
    }

    // Normal filtering varies per hit, so it intentionally stays outside the
    // component cache.
    if (!base.excluded && m_config.walkable_min_normal_z > 0.0 && hit.normal_z < m_config.walkable_min_normal_z)
    {
        base.excluded = true;
        base.landscape = false;
    }
    return base;
}

std::optional<SquadHeightRuntimeMod::AcceptedHit> SquadHeightRuntimeMod::trace_first_acceptable(double x_cm, double y_cm, double z_start_cm)
{
    double current_start = z_start_cm;
    const double epsilon_cm = m_config.retrace_epsilon_m * CmPerMeter;

    for (int attempt = 0; attempt < m_config.max_hits_per_column; ++attempt)
    {
        if (current_start <= m_z_bottom_cm) return std::nullopt;

        TraceHit hit{};
        std::string error;
        bool was_hit = m_tracer.trace(x_cm, y_cm, current_start, m_z_bottom_cm, m_config.trace_complex, hit, error);
        ++m_stats.trace_calls;
        if (!error.empty()) throw std::runtime_error(error);

        if (!was_hit && m_config.trace_complex && m_config.fallback_simple_trace)
        {
            error.clear();
            was_hit = m_tracer.trace(x_cm, y_cm, current_start, m_z_bottom_cm, false, hit, error);
            ++m_stats.trace_calls;
            if (!error.empty()) throw std::runtime_error(error);
        }
        if (!was_hit) return std::nullopt;

        const auto classification = classify_hit(hit);
        if (!classification.excluded) return AcceptedHit{hit.z_cm, classification.landscape};

        ++m_stats.filtered_hits;
        current_start = hit.z_cm - epsilon_cm;
    }

    ++m_stats.max_hit_columns;
    return std::nullopt;
}

std::optional<SquadHeightRuntimeMod::ColumnSample> SquadHeightRuntimeMod::sample_column(double x_cm, double y_cm)
{
    auto first = trace_first_acceptable(x_cm, y_cm, m_z_top_cm);
    if (!first) return std::nullopt;

    if (m_config.surface_mode != "terrain_under_overhang") return ColumnSample{first->z_cm, !first->landscape};

    AcceptedHit current = *first;
    const double epsilon_cm = m_config.retrace_epsilon_m * CmPerMeter;
    const double min_clearance_cm = m_config.overhang_min_clearance_m * CmPerMeter;

    for (int i = 0; i < m_config.max_hits_per_column; ++i)
    {
        if (current.landscape) break;
        auto below = trace_first_acceptable(x_cm, y_cm, current.z_cm - epsilon_cm);
        if (!below) break;

        if ((current.z_cm - below->z_cm) >= min_clearance_cm)
        {
            current = *below;
            ++m_stats.overhang_drops;
        }
        else
        {
            break;
        }
    }
    return ColumnSample{current.z_cm, !current.landscape};
}

void SquadHeightRuntimeMod::process_chunk()
{
    const auto chunk_start = std::chrono::steady_clock::now();
    std::size_t chunk_cells = 0;

    try
    {
        while (m_row < m_rows)
        {
            if (m_cancel_requested.load(std::memory_order_acquire))
            {
                cancel_scan("Export cancelled by F8.");
                return;
            }

            const double v = static_cast<double>(m_row) * m_step_cm - m_half_v;
            const double u = static_cast<double>(m_col) * m_step_cm - m_half_u;
            const double x = m_center_x + u * m_cos_r - v * m_sin_r;
            const double y = m_center_y + u * m_sin_r + v * m_cos_r;

            const auto sample = sample_column(x, y);
            float z_m = std::numeric_limits<float>::quiet_NaN();
            if (!sample)
            {
                ++m_stats.no_hit_cells;
            }
            else
            {
                const double world_z_m = sample->z_cm / CmPerMeter;
                z_m = static_cast<float>(world_z_m);
                m_world_z_min_m = std::min(m_world_z_min_m, world_z_m);
                m_world_z_max_m = std::max(m_world_z_max_m, world_z_m);
                if (sample->structure) ++m_stats.structure_cells;
            }

            m_row_buffer[m_col] = z_m;
            ++m_col;
            ++m_cells_done;
            ++chunk_cells;

            if (m_col >= m_cols)
            {
                m_raw_file.write(reinterpret_cast<const char*>(m_row_buffer.data()), static_cast<std::streamsize>(m_cols * sizeof(float)));
                if (!m_raw_file) throw std::runtime_error("raw raster write failed");
                m_col = 0;
                ++m_row;
            }

            if (chunk_cells >= m_config.max_cells_per_tick) break;
            const auto elapsed_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - chunk_start).count();
            if (elapsed_ms >= m_config.frame_budget_ms) break;
        }
    }
    catch (const std::exception& e)
    {
        abort_scan(std::string("Scan error: ") + e.what());
        return;
    }

    if (m_row >= m_rows)
    {
        begin_postprocess();
        return;
    }

    const auto now = std::chrono::steady_clock::now();
    if (std::chrono::duration<double>(now - m_last_progress).count() >= 2.0)
    {
        m_last_progress = now;
        const double elapsed = std::max(std::chrono::duration<double>(now - m_scan_started).count(), 0.001);
        const double rate = static_cast<double>(m_cells_done) / elapsed;
        const double remaining = static_cast<double>(m_stats.total_cells - m_cells_done) / std::max(rate, 0.001);
        const double pct = static_cast<double>(m_cells_done) * 100.0 / static_cast<double>(m_stats.total_cells);
        log(std::format("{:.1f}% | row {}/{} | {:.0f} cells/s | {:.0f}s remaining | traces={} filtered={} vegComp={} | compCache={} actorCache={}",
                        pct, m_row + 1, m_rows, rate, remaining, m_stats.trace_calls, m_stats.filtered_hits,
                        m_stats.vegetation_components_filtered, m_component_cache.size(), m_actor_cache.size()));
    }
}

void SquadHeightRuntimeMod::begin_postprocess()
{
    if (m_raw_file.is_open())
    {
        m_raw_file.flush();
        m_raw_file.close();
    }

    if (!std::isfinite(m_world_z_min_m) || !std::isfinite(m_world_z_max_m))
    {
        abort_scan("No geometry was hit at all. Check trace channel / map bounds.");
        return;
    }

    const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - m_scan_started).count();
    m_stats.scan_seconds = std::floor(elapsed * 10.0 + 0.5) / 10.0;

    Snapshot snapshot{};
    snapshot.map_name = m_map_name;
    snapshot.world_name = m_world_name;
    snapshot.output_dir = m_output_dir;
    snapshot.raw_path = m_raw_path;
    snapshot.full_json_path = m_full_json_path;
    snapshot.downsample_path = m_downsample_path;
    snapshot.meta_path = m_meta_path;
    snapshot.bounds = m_bounds;
    snapshot.resolution_m = m_config.resolution_m;
    snapshot.rows = m_rows;
    snapshot.cols = m_cols;
    snapshot.rotation_deg = m_rotation_deg;
    snapshot.z_offset_m = m_world_z_min_m;
    snapshot.world_z_min_m = m_world_z_min_m;
    snapshot.world_z_max_m = m_world_z_max_m;
    snapshot.transpose = m_config.transpose;
    snapshot.flip_rows = m_config.flip_rows;
    snapshot.flip_cols = m_config.flip_cols;
    snapshot.surface_mode = m_config.surface_mode;
    snapshot.trace_type_query = m_config.trace_type_query;
    snapshot.trace_complex = m_config.trace_complex;
    snapshot.fallback_simple_trace = m_config.fallback_simple_trace;
    snapshot.trace_top_m = m_config.trace_top_m;
    snapshot.trace_bottom_m = m_config.trace_bottom_m;
    snapshot.keep_raw = m_config.keep_raw;
    snapshot.write_full_json = m_config.write_full_json;
    snapshot.downsample_to = m_config.downsample_to;
    snapshot.stats = m_stats;

    m_phase.store(Phase::Finalizing, std::memory_order_release);
    log(std::format("Scan complete in {:.1f}s. Starting native file post-process asynchronously.", snapshot.stats.scan_seconds));

    m_finalizer = std::jthread([this, snapshot = std::move(snapshot)](std::stop_token stop) {
        std::string error;
        const bool ok = finalize_snapshot(snapshot, stop, [this](const std::string& line) { log(line); }, error);
        if (ok)
        {
            log(std::format("DONE: {} | {:.2f}..{:.2f} m world Z | {} cells | {} trace calls",
                            snapshot.map_name, snapshot.world_z_min_m, snapshot.world_z_max_m,
                            snapshot.stats.total_cells, snapshot.stats.trace_calls));
            log("Output: " + snapshot.output_dir.string());
        }
        else
        {
            log("WARNING: Post-process failed: " + error);
        }
        close_log();
        m_phase.store(Phase::Idle, std::memory_order_release);
    });
}

void SquadHeightRuntimeMod::cancel_scan(const std::string& reason)
{
    if (m_raw_file.is_open()) m_raw_file.close();
    log(reason);
    std::error_code ec;
    if (!m_raw_path.empty()) std::filesystem::remove(m_raw_path, ec);
    m_phase.store(Phase::Idle, std::memory_order_release);
    m_cancel_requested.store(false, std::memory_order_release);
    close_log();
}

void SquadHeightRuntimeMod::abort_scan(const std::string& reason)
{
    if (m_raw_file.is_open()) m_raw_file.close();
    log("WARNING: " + reason);
    std::error_code ec;
    if (!m_raw_path.empty()) std::filesystem::remove(m_raw_path, ec);
    m_phase.store(Phase::Idle, std::memory_order_release);
    m_cancel_requested.store(false, std::memory_order_release);
    close_log();
}

void SquadHeightRuntimeMod::log(const std::string& message)
{
    Output::send(STR("[SquadHeightRuntimeCpp] {}\n"), RC::to_wstring(message));
    std::scoped_lock lock(m_log_mutex);
    if (m_log_file.is_open())
    {
        const auto now = std::chrono::system_clock::now();
        const auto t = std::chrono::system_clock::to_time_t(now);
        std::tm tm{};
        localtime_s(&tm, &t);
        char time_buffer[32];
        std::strftime(time_buffer, sizeof(time_buffer), "%Y-%m-%d %H:%M:%S", &tm);
        m_log_file << time_buffer << " [SquadHeightRuntimeCpp] " << message << '\n';
        m_log_file.flush();
    }
}

void SquadHeightRuntimeMod::close_log()
{
    std::scoped_lock lock(m_log_mutex);
    if (m_log_file.is_open())
    {
        m_log_file.flush();
        m_log_file.close();
    }
}
} // namespace SquadHeight
