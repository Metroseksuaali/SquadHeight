#include "Maps.hpp"

#include <array>
#include <string>

namespace SquadHeight
{
namespace
{
constexpr std::array<MapDef, 60> Maps{{
    // Bounds are {min_x, max_x, min_y, max_y} in meters (UE world XY).
    //
    // Vanilla Squad maps: canonical SquadCalc minimap bounds, identical to
    // tools/squadcalc_bounds.json and ue4ss/SquadHeightRuntime/Scripts/maps.lua.
    {"AlBasrah", {-2000, 2000, -2000, 2000}},
    {"Anvil", {-2040, 1020, -2040, 1020}},
    {"Belaya", {-1954, 1950, -2080, 1825}},
    {"BlackCoast", {-2299, 2299, -2127, 2472}},
    {"Chora", {-2464, 1600, -2664, 1400}},
    {"Fallujah", {-1315, 1690, -1545, 1460}},
    {"FoolsRoad", {-1326, 448, -1326, 448}},
    {"GooseBay", {-2016, 2015, -2016, 2015}},
    {"Gorodok", {-2032, 2032, -2032, 2032}},
    {"Harju", {-2016, 2016, -2016, 2016}},
    {"Jensen", {-2004, 2004, -2004, 2004}},
    {"Kamdesh", {-2016, 2016, -2016, 2016}},
    {"Kohat", {-2300, 2317, -2300, 2317}},
    {"Kokan", {-1076, 1420, -1076, 1420}},
    {"Lashkar", {-2167, 2167, -2167, 2167}},
    {"Logar", {-881, 880, -1132, 629}},
    {"Manicouagan", {-2016, 2015, -2016, 2015}},
    {"Mestia", {-1200, 1200, -1100, 1300}},
    {"Mutaha", {-935, 1820, -1140, 1615}},
    {"Narva", {-1390, 1410, -1402, 1398}},
    {"Narva_f", {-1390, 1410, -1402, 1398}},
    {"Pacific", {-2016, 2016, -2016, 2016}},
    {"Sanxian", {-2300, 2300, -2050, 2550}},
    {"Skorpo", {-3611, 3238, -3293, 3576}},
    {"Sumari", {-640, 660, -447, 853}},
    {"Tallil", {-2340, 2340, -2340, 2340}},
    {"Yehorivka", {-3302, 3048, -3302, 3048}},
    //
    // Not from SquadCalc: hand-derived bounds, not verified against the
    // SquadCalc minimap. Kept out of maps.lua for that reason.
    {"Chornivsk", {-1649, 1535, -1547, 1637}},
    {"Hrodna_Border", {-2016, 2016, -2016, 2016}},
    {"AlBasrah_legacy", {-1520, 1520, -1520, 1520}},
    //
    // ---- Modded / community maps start here ----
    // Not part of SquadHeight's released data; bounds supplied by contributors.
    {"Bespin", {-2031.92, 2031.92, -2031.92, 2031.92}},
    {"Coruscant", {-2000, 2000, -2000, 2000}},
    {"Corvette", {-166.20, -6.65, -78.11, 81.44}},
    {"Felucia", {-789.93, 1876.70, -3999.91, -1333.21}},
    {"Galban", {-1784.99, 1784.97, -1784.97, 1784.91}},
    {"Geonosis", {-2015.67, 2015.7, -2015.67, 2015.7}},
    {"Kashyyyk", {-2106.43, 1893.56, -2814.39, 1185.59}},
    {"Kavado", {-1072.36, 2052.09, -2098.28, 1026.17}},
    {"Mallidon", {-2400, -400, -2400, -400}},
    {"Miniosis", {-1020, 1020, -1020, 1020}},
    {"Mininosis", {-1020, 1020, -1020, 1020}}, // Runtime alias used by layers such as Mininosis_AAS_V1.
    {"Morak", {-3250, 3250, -3250, 3250}},
    {"MorakLegacy", {-2102.47, 2102.47, -2301.85, 2301.85}},
    {"Mygeeto", {-2000, 2000, -2000, 2000}},
    {"NabooPlains", {-1967.98, 1968.98, -1967.98, 1968.98}},
    {"Ortoplutonia", {-1999.95, 2000.05, -1999.95, 2000.05}},
    {"Rhenvar", {-4079.85, 4053.6, -4079.85, 4053.6}},
    {"Ryloth", {-620, 620, -620, 620}},
    {"Sesid", {-1880, 1880, -1880, 1880}},
    {"SesidEquator", {-2400, 2400, -2400, 2400}},
    {"Sullust", {-1078.77, 1079.56, -1078.77, 1079.56}},
    {"Tatooine", {-1966.20, 1579.80, -1865.35, 1680.65}},
    {"Umbara", {-800, 800, -800, 800}},
    {"VenatorAssault", {-400.3, 399.7, -399.6, 400.4}},
    {"VenatorAssault2", {-855.4, 644.6, -462, 1038}},
    {"Yavin4", {-1260.02, 1260.02, -1260.02, 1260.02}},
    // ---- End of modded / community maps ----
    //
    // Extra aliases retained from the Lua backup / common runtime naming.
    {"HrodnaBorder", {-2015, 2015, -2015, 2016}},
    {"Hrodna", {-2016, 2016, -2016, 2016}},
    {"AlBasrahLegacy", {-1520, 1520, -1520, 1520}},
    {"JensensRange", {-2004, 2004, -2004, 2004}},
}};

std::string normalize_world_name(std::string value)
{
    // Strip UEDPIE_<n>_ if present.
    if (value.rfind("UEDPIE_", 0) == 0)
    {
        const auto second_underscore = value.find('_', 7);
        if (second_underscore != std::string::npos) value.erase(0, second_underscore + 1);
    }
    return lower_ascii(std::move(value));
}
} // namespace

const MapDef* find_map_exact(std::string_view map_id)
{
    const auto low_id = lower_ascii(std::string(map_id));
    for (const auto& map : Maps)
    {
        if (lower_ascii(map.name) == low_id) return &map;
    }
    return nullptr;
}

MapIdLookup find_map_id(std::string_view map_id)
{
    if (const auto* exact = find_map_exact(map_id))
    {
        return {exact, std::string(map_id), false};
    }

    const auto separator = map_id.rfind('_');
    if (separator != std::string_view::npos && separator + 1 < map_id.size())
    {
        std::string suffix(map_id.substr(separator + 1));
        if (const auto* normalized = find_map_exact(suffix))
        {
            return {normalized, std::move(suffix), true};
        }
    }
    return {};
}

const MapDef* detect_map(std::string_view world_name)
{
    const auto low_world = normalize_world_name(std::string(world_name));
    const MapDef* best = nullptr;
    for (const auto& map : Maps)
    {
        const auto low_name = lower_ascii(map.name);
        if (low_world.find(low_name) == std::string::npos) continue;
        if (!best || std::char_traits<char>::length(map.name) > std::char_traits<char>::length(best->name)) best = &map;
    }
    return best;
}
} // namespace SquadHeight
