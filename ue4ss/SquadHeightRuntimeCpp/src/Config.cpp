#include "Config.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <stdexcept>
#include <string_view>
#include <unordered_map>

namespace SquadHeight
{
namespace
{
std::string trim(std::string value)
{
    auto not_space = [](unsigned char c) { return !std::isspace(c); };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), not_space));
    value.erase(std::find_if(value.rbegin(), value.rend(), not_space).base(), value.end());
    return value;
}

std::vector<std::string> split_csv(const std::string& value)
{
    std::vector<std::string> out;
    std::size_t start = 0;
    while (start <= value.size())
    {
        const auto comma = value.find(',', start);
        auto part = trim(value.substr(start, comma == std::string::npos ? std::string::npos : comma - start));
        if (!part.empty()) out.emplace_back(std::move(part));
        if (comma == std::string::npos) break;
        start = comma + 1;
    }
    return out;
}

bool parse_bool(const std::string& raw)
{
    const auto value = lower_ascii(trim(raw));
    if (value == "1" || value == "true" || value == "yes" || value == "on") return true;
    if (value == "0" || value == "false" || value == "no" || value == "off") return false;
    throw std::runtime_error("expected boolean, got '" + raw + "'");
}

double parse_double(const std::string& raw)
{
    std::size_t used = 0;
    const double value = std::stod(trim(raw), &used);
    if (used != trim(raw).size()) throw std::runtime_error("expected number, got '" + raw + "'");
    return value;
}

long long parse_int(const std::string& raw)
{
    std::size_t used = 0;
    const auto text = trim(raw);
    const long long value = std::stoll(text, &used, 10);
    if (used != text.size()) throw std::runtime_error("expected integer, got '" + raw + "'");
    return value;
}

using IniMap = std::unordered_map<std::string, std::string>;

IniMap parse_ini(const std::filesystem::path& path)
{
    std::ifstream file(path);
    if (!file) throw std::runtime_error("could not open " + path.string());

    IniMap values;
    std::string section;
    std::string line;
    std::size_t line_no = 0;
    while (std::getline(file, line))
    {
        ++line_no;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        auto text = trim(line);
        if (text.empty() || text[0] == ';' || text[0] == '#') continue;

        if (text.front() == '[' && text.back() == ']')
        {
            section = lower_ascii(trim(text.substr(1, text.size() - 2)));
            continue;
        }

        const auto eq = text.find('=');
        if (eq == std::string::npos)
        {
            throw std::runtime_error("invalid INI line " + std::to_string(line_no));
        }

        auto key = lower_ascii(trim(text.substr(0, eq)));
        auto value = trim(text.substr(eq + 1));
        if (const auto semicolon = value.find(';'); semicolon != std::string::npos)
        {
            value = trim(value.substr(0, semicolon));
        }
        values[section + "." + key] = value;
    }
    return values;
}

const std::string* find_value(const IniMap& values, std::string_view section, std::string_view key)
{
    const auto it = values.find(std::string(section) + "." + std::string(key));
    return it == values.end() ? nullptr : &it->second;
}

template <typename Setter>
void apply(const IniMap& values, std::string_view section, std::string_view key, Setter&& setter, std::vector<std::string>& warnings)
{
    const auto* value = find_value(values, section, key);
    if (!value) return;
    try
    {
        setter(*value);
    }
    catch (const std::exception& e)
    {
        warnings.emplace_back(std::string(section) + "." + std::string(key) + ": " + e.what());
    }
}
} // namespace

std::string lower_ascii(std::string value)
{
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return value;
}

bool contains_ci(const std::string& haystack, const std::string& needle)
{
    if (needle.empty()) return true;
    return lower_ascii(haystack).find(lower_ascii(needle)) != std::string::npos;
}

bool starts_with_ci(const std::string& value, const std::string& prefix)
{
    if (prefix.size() > value.size()) return false;
    return std::equal(prefix.begin(), prefix.end(), value.begin(), [](unsigned char a, unsigned char b) {
        return std::tolower(a) == std::tolower(b);
    });
}

Config Config::load(const std::filesystem::path& path, std::vector<std::string>& warnings)
{
    Config cfg;
    if (!std::filesystem::exists(path))
    {
        warnings.emplace_back("config.ini not found; built-in defaults are active");
        return cfg;
    }

    IniMap values;
    try
    {
        values = parse_ini(path);
    }
    catch (const std::exception& e)
    {
        warnings.emplace_back(std::string("config.ini parse failed: ") + e.what() + "; built-in defaults are active");
        return cfg;
    }

    apply(values, "scan", "resolution_m", [&](const auto& v) { cfg.resolution_m = parse_double(v); }, warnings);
    apply(values, "scan", "trace_top_m", [&](const auto& v) { cfg.trace_top_m = parse_double(v); }, warnings);
    apply(values, "scan", "trace_bottom_m", [&](const auto& v) { cfg.trace_bottom_m = parse_double(v); }, warnings);
    apply(values, "scan", "trace_type_query", [&](const auto& v) { cfg.trace_type_query = static_cast<int>(parse_int(v)); }, warnings);
    apply(values, "scan", "trace_complex", [&](const auto& v) { cfg.trace_complex = parse_bool(v); }, warnings);
    apply(values, "scan", "fallback_simple_trace", [&](const auto& v) { cfg.fallback_simple_trace = parse_bool(v); }, warnings);
    apply(values, "scan", "surface_mode", [&](const auto& v) { cfg.surface_mode = lower_ascii(trim(v)); }, warnings);
    apply(values, "scan", "overhang_min_clearance_m", [&](const auto& v) { cfg.overhang_min_clearance_m = parse_double(v); }, warnings);
    apply(values, "scan", "retrace_epsilon_m", [&](const auto& v) { cfg.retrace_epsilon_m = parse_double(v); }, warnings);
    apply(values, "scan", "max_hits_per_column", [&](const auto& v) { cfg.max_hits_per_column = static_cast<int>(parse_int(v)); }, warnings);
    apply(values, "scan", "walkable_min_normal_z", [&](const auto& v) { cfg.walkable_min_normal_z = parse_double(v); }, warnings);
    apply(values, "scan", "max_cells_per_tick", [&](const auto& v) { cfg.max_cells_per_tick = static_cast<std::size_t>(std::max<long long>(1, parse_int(v))); }, warnings);
    apply(values, "scan", "frame_budget_ms", [&](const auto& v) { cfg.frame_budget_ms = parse_double(v); }, warnings);

    apply(values, "output", "output_root", [&](const auto& v) { cfg.output_root = std::filesystem::path(trim(v)); }, warnings);
    apply(values, "output", "keep_raw", [&](const auto& v) { cfg.keep_raw = parse_bool(v); }, warnings);
    apply(values, "output", "write_full_json", [&](const auto& v) { cfg.write_full_json = parse_bool(v); }, warnings);
    apply(values, "output", "downsample_to", [&](const auto& v) { cfg.downsample_to = static_cast<int>(parse_int(v)); }, warnings);
    apply(values, "output", "output_name", [&](const auto& v) { cfg.output_name = trim(v); }, warnings);

    apply(values, "orientation", "transpose", [&](const auto& v) { cfg.transpose = parse_bool(v); }, warnings);
    apply(values, "orientation", "flip_rows", [&](const auto& v) { cfg.flip_rows = parse_bool(v); }, warnings);
    apply(values, "orientation", "flip_cols", [&](const auto& v) { cfg.flip_cols = parse_bool(v); }, warnings);
    apply(values, "orientation", "grid_rotation_deg", [&](const auto& v) { cfg.grid_rotation_deg = parse_double(v); }, warnings);

    apply(values, "manual_map", "name", [&](const auto& v) { cfg.manual_map_name = trim(v); }, warnings);
    bool have_min_x = false, have_max_x = false, have_min_y = false, have_max_y = false;
    apply(values, "manual_map", "min_x", [&](const auto& v) { cfg.manual_bounds.min_x = parse_double(v); have_min_x = true; }, warnings);
    apply(values, "manual_map", "max_x", [&](const auto& v) { cfg.manual_bounds.max_x = parse_double(v); have_max_x = true; }, warnings);
    apply(values, "manual_map", "min_y", [&](const auto& v) { cfg.manual_bounds.min_y = parse_double(v); have_min_y = true; }, warnings);
    apply(values, "manual_map", "max_y", [&](const auto& v) { cfg.manual_bounds.max_y = parse_double(v); have_max_y = true; }, warnings);
    cfg.manual_bounds_enabled = !cfg.manual_map_name.empty() && have_min_x && have_max_x && have_min_y && have_max_y;

    apply(values, "filters", "exclude_actor_classes", [&](const auto& v) { cfg.exclude_actor_classes = split_csv(v); }, warnings);
    apply(values, "filters", "exclude_actor_base_classes", [&](const auto& v) { cfg.exclude_actor_base_classes = split_csv(v); }, warnings);
    apply(values, "filters", "exclude_actor_class_prefixes", [&](const auto& v) { cfg.exclude_actor_class_prefixes = split_csv(v); }, warnings);
    apply(values, "filters", "exclude_component_classes", [&](const auto& v) { cfg.exclude_component_classes = split_csv(v); }, warnings);
    apply(values, "filters", "pcg_vegetation_actor_name_prefixes", [&](const auto& v) { cfg.pcg_vegetation_actor_name_prefixes = split_csv(v); }, warnings);
    apply(values, "filters", "pcg_vegetation_require_actor_prefix", [&](const auto& v) { cfg.pcg_vegetation_require_actor_prefix = parse_bool(v); }, warnings);
    apply(values, "filters", "pcg_vegetation_component_name_bases", [&](const auto& v) { cfg.pcg_vegetation_component_name_bases = split_csv(v); }, warnings);
    apply(values, "filters", "volume_shape_component_classes", [&](const auto& v) { cfg.volume_shape_component_classes = split_csv(v); }, warnings);
    apply(values, "filters", "exclude_asset_path_keywords", [&](const auto& v) { cfg.exclude_asset_path_keywords = split_csv(v); }, warnings);
    apply(values, "filters", "volume_actor_name_keywords", [&](const auto& v) { cfg.volume_actor_name_keywords = split_csv(v); }, warnings);

    if (!(cfg.resolution_m > 0.0)) { warnings.emplace_back("resolution_m must be > 0; using 1.0"); cfg.resolution_m = 1.0; }
    if (!(cfg.trace_top_m > cfg.trace_bottom_m)) { warnings.emplace_back("trace_top_m must be > trace_bottom_m; using +/-5000 m"); cfg.trace_top_m = 5000.0; cfg.trace_bottom_m = -5000.0; }
    if (cfg.trace_type_query < 0 || cfg.trace_type_query > 255) { warnings.emplace_back("trace_type_query must be 0..255; using 0"); cfg.trace_type_query = 0; }
    if (cfg.surface_mode != "topmost" && cfg.surface_mode != "terrain_under_overhang") { warnings.emplace_back("surface_mode must be topmost or terrain_under_overhang; using topmost"); cfg.surface_mode = "topmost"; }
    if (cfg.max_hits_per_column < 1) { warnings.emplace_back("max_hits_per_column must be >= 1; using 16"); cfg.max_hits_per_column = 16; }
    if (!(cfg.frame_budget_ms > 0.0)) { warnings.emplace_back("frame_budget_ms must be > 0; using 45"); cfg.frame_budget_ms = 45.0; }
    cfg.walkable_min_normal_z = std::clamp(cfg.walkable_min_normal_z, -1.0, 1.0);
    if (cfg.downsample_to < 0 || cfg.downsample_to == 1) { warnings.emplace_back("downsample_to must be 0 or >=2; using 500"); cfg.downsample_to = 500; }
    if (cfg.manual_bounds_enabled && !(cfg.manual_bounds.max_x > cfg.manual_bounds.min_x && cfg.manual_bounds.max_y > cfg.manual_bounds.min_y))
    {
        warnings.emplace_back("manual map bounds are invalid; automatic map detection will be used");
        cfg.manual_bounds_enabled = false;
    }

    return cfg;
}
} // namespace SquadHeight
