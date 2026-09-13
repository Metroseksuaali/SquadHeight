#pragma once

#include "Config.hpp"
#include "Postprocess.hpp"
#include "TraceInvoker.hpp"

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <thread>
#include <limits>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <Mod/CppUserModBase.hpp>
#include <UnrealDef.hpp>

namespace SquadHeight
{
class SquadHeightRuntimeMod final : public RC::CppUserModBase
{
public:
    SquadHeightRuntimeMod();
    ~SquadHeightRuntimeMod() override;

    auto on_unreal_init() -> void override;

private:
    enum class Phase : std::uint8_t
    {
        Idle,
        Scanning,
        Finalizing,
    };

    struct ActorInfo
    {
        bool excluded{};
        bool landscape{};
        bool has_static_mesh{};
        bool has_static_mesh_known{};
    };

    struct ComponentInfo
    {
        bool excluded{};
        bool landscape{};
    };

    struct AcceptedHit
    {
        double z_cm{};
        bool landscape{};
    };

    struct ColumnSample
    {
        double z_cm{};
        bool structure{};
    };

    struct RuntimeLayerIdentity
    {
        std::string layer_name;
        std::string level_id;
        std::string matched_world_path;
        std::string match_method;
    };

    void game_tick();
    void handle_toggle();
    void start_scan();
    void process_chunk();
    void cancel_scan(const std::string& reason);
    void abort_scan(const std::string& reason);
    void begin_postprocess();

    std::optional<AcceptedHit> trace_first_acceptable(double x_cm, double y_cm, double z_start_cm);
    std::optional<ColumnSample> sample_column(double x_cm, double y_cm);
    ComponentInfo classify_hit(const TraceHit& hit);
    RC::UObject* resolve_actor(const TraceHit& hit) const;
    ActorInfo& actor_info(RC::UObject* actor);
    bool actor_has_static_mesh(RC::UObject* actor, ActorInfo& info);
    bool actor_is_excluded(RC::UObject* actor) const;
    bool actor_is_landscape(RC::UObject* actor) const;
    bool component_is_excluded(RC::UObject* actor, RC::UObject* component);
    bool is_known_pcg_vegetation(RC::UObject* actor, RC::UObject* component) const;
    std::string component_mesh_path(RC::UObject* component) const;

    void resolve_filter_classes();
    RC::UClass* find_class(const std::string& path) const;
    bool class_is_actor(RC::UObject* object) const;
    std::optional<RuntimeLayerIdentity> resolve_runtime_layer_identity();

    std::filesystem::path module_directory() const;
    void setup_output_paths();
    static std::string safe_path_component(std::string value);
    static std::string normalize_world_name(std::string value);
    static std::string object_name(RC::UObject* object);
    static std::string class_name(RC::UObject* object);

    void log(const std::string& message);
    void close_log();

private:
    std::atomic<bool> m_toggle_requested{false};
    std::atomic<Phase> m_phase{Phase::Idle};
    std::atomic<bool> m_cancel_requested{false};
    bool m_tick_seen{};
    bool m_f8_was_down{};

    std::filesystem::path m_mod_directory;
    Config m_config{};
    TraceInvoker m_tracer{};
    bool m_trace_ready{};

    RC::UClass* m_actor_class{};
    RC::UClass* m_static_mesh_component_class{};
    RC::UClass* m_landscape_proxy_class{};
    std::vector<RC::UClass*> m_excluded_base_classes;

    std::unordered_set<std::string> m_excluded_actor_classes;
    std::unordered_set<std::string> m_excluded_component_classes;
    std::unordered_set<std::string> m_volume_component_classes;
    std::vector<std::string> m_actor_class_prefixes_lower;
    std::vector<std::string> m_pcg_actor_prefixes_lower;
    std::vector<std::string> m_pcg_component_bases_lower;
    std::vector<std::string> m_asset_keywords_lower;

    std::unordered_map<RC::UObject*, ActorInfo> m_actor_cache;
    std::unordered_map<RC::UObject*, ComponentInfo> m_component_cache;

    RC::UObject* m_world{};
    RC::UObject* m_world_context{};
    std::string m_world_name;
    std::string m_map_name;
    Bounds m_bounds{};

    std::filesystem::path m_output_dir;
    std::filesystem::path m_raw_path;
    std::filesystem::path m_full_json_path;
    std::filesystem::path m_downsample_path;
    std::filesystem::path m_meta_path;
    std::filesystem::path m_log_path;

    std::ofstream m_raw_file;
    std::ofstream m_log_file;
    std::mutex m_log_mutex;
    std::jthread m_finalizer;

    double m_step_cm{};
    double m_center_x{};
    double m_center_y{};
    double m_half_u{};
    double m_half_v{};
    double m_cos_r{1.0};
    double m_sin_r{};
    double m_rotation_deg{};
    double m_z_top_cm{};
    double m_z_bottom_cm{};

    std::size_t m_rows{};
    std::size_t m_cols{};
    std::size_t m_row{};
    std::size_t m_col{};
    std::uint64_t m_cells_done{};
    std::vector<float> m_row_buffer;

    double m_world_z_min_m{std::numeric_limits<double>::infinity()};
    double m_world_z_max_m{-std::numeric_limits<double>::infinity()};
    ScanStats m_stats{};
    std::chrono::steady_clock::time_point m_scan_started{};
    std::chrono::steady_clock::time_point m_last_progress{};
};
} // namespace SquadHeight
