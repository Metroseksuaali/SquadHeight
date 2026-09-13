#pragma once

#include <cstddef>
#include <filesystem>
#include <string>
#include <vector>

namespace SquadHeight
{
struct Bounds
{
    double min_x{};
    double max_x{};
    double min_y{};
    double max_y{};
};

struct Config
{
    double resolution_m{1.0};
    double trace_top_m{5000.0};
    double trace_bottom_m{-5000.0};
    int trace_type_query{0};
    bool trace_complex{true};
    bool fallback_simple_trace{true};

    std::string surface_mode{"topmost"};
    double overhang_min_clearance_m{2.5};
    double retrace_epsilon_m{0.05};
    int max_hits_per_column{16};
    double walkable_min_normal_z{0.0};

    std::size_t max_cells_per_tick{100000};
    double frame_budget_ms{45.0};

    std::filesystem::path output_root{"SquadHeight_output"};
    bool keep_raw{true};
    bool write_full_json{true};
    int downsample_to{500};

    bool transpose{false};
    bool flip_rows{false};
    bool flip_cols{false};
    double grid_rotation_deg{0.0};

    std::string output_name{};
    std::string manual_map_name{};
    bool manual_bounds_enabled{false};
    Bounds manual_bounds{};

    std::vector<std::string> exclude_actor_classes{"InstancedFoliageActor"};
    std::vector<std::string> exclude_actor_base_classes{
        "/Script/Engine.Pawn",
        "/Script/Squad.SQVehicle",
        "/Script/Squad.SQDeployable",
    };
    std::vector<std::string> exclude_actor_class_prefixes{"BP_POI_Reference"};
    std::vector<std::string> exclude_component_classes{
        "FoliageInstancedStaticMeshComponent",
        "LandscapeGrassComponent",
    };
    std::vector<std::string> pcg_vegetation_actor_name_prefixes{"PCGStamp_"};
    bool pcg_vegetation_require_actor_prefix{false};
    std::vector<std::string> pcg_vegetation_component_name_bases{
        "ISM_SM_Birch_Large02",
        "ISM_SM_Birchcluster_01",
        "ISM_SM_BirchLarge03",
        "ISM_SM_GrassRiverCluster_medl01",
        "ISM_SM_GrassRiverCluster_medl02",
        "ISM_SM_Oakshrub01",
        "ISM_SM_Oakshrub02",
        "ISM_SM_Reeds02",
        "ISM_SM_Reeds03",
        "ISM_SM_Reeds04",
        "ISM_SM_Reeds05",
        "ISM_SM_scotspine_large01",
        "ISM_SM_Scotspine_mid01",
        "ISM_SM_Scotspine_small01",
    };
    std::vector<std::string> volume_shape_component_classes{
        "BoxComponent",
        "SphereComponent",
        "CapsuleComponent",
        "BrushComponent",
    };
    std::vector<std::string> exclude_asset_path_keywords{"foliage", "surroundmesh"};
    std::vector<std::string> volume_actor_name_keywords{"volume", "boundary", "waterblock"};

    static Config load(const std::filesystem::path& path, std::vector<std::string>& warnings);
};

std::string lower_ascii(std::string value);
bool contains_ci(const std::string& haystack, const std::string& needle);
bool starts_with_ci(const std::string& value, const std::string& prefix);
} // namespace SquadHeight
