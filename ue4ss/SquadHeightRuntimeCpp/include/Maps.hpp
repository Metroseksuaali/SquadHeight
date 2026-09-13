#pragma once

#include "Config.hpp"

#include <optional>
#include <string>
#include <string_view>

namespace SquadHeight
{
struct MapDef
{
    const char* name;
    Bounds bounds;
    std::optional<double> grid_rotation_deg{};
};

struct MapIdLookup
{
    const MapDef* map{};
    std::string lookup_id;
    bool used_last_underscore_suffix{};
};

const MapDef* find_map_exact(std::string_view map_id);
MapIdLookup find_map_id(std::string_view map_id);
const MapDef* detect_map(std::string_view world_name);
} // namespace SquadHeight
