#pragma once

#include "Config.hpp"

#include <cstdint>
#include <filesystem>
#include <functional>
#include <stop_token>
#include <string>

namespace SquadHeight
{
struct ScanStats
{
    std::uint64_t total_cells{};
    std::uint64_t trace_calls{};
    std::uint64_t filtered_hits{};
    std::uint64_t vegetation_components_filtered{};
    std::uint64_t structure_cells{};
    std::uint64_t no_hit_cells{};
    std::uint64_t max_hit_columns{};
    std::uint64_t overhang_drops{};
    double scan_seconds{};
};

struct Snapshot
{
    std::string map_name;
    std::string world_name;
    std::filesystem::path output_dir;
    std::filesystem::path raw_path;
    std::filesystem::path full_json_path;
    std::filesystem::path downsample_path;
    std::filesystem::path meta_path;

    Bounds bounds{};
    double resolution_m{};
    std::size_t rows{};
    std::size_t cols{};
    double rotation_deg{};

    double z_offset_m{};
    double world_z_min_m{};
    double world_z_max_m{};

    bool transpose{};
    bool flip_rows{};
    bool flip_cols{};

    std::string surface_mode;
    int trace_type_query{};
    bool trace_complex{};
    bool fallback_simple_trace{};
    double trace_top_m{};
    double trace_bottom_m{};

    bool keep_raw{};
    bool write_full_json{};
    int downsample_to{};
    ScanStats stats{};
};

using LogCallback = std::function<void(const std::string&)>;

bool finalize_snapshot(const Snapshot& snapshot, std::stop_token stop_token, const LogCallback& log, std::string& error);
} // namespace SquadHeight
