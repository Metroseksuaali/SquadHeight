#define NOMINMAX
#include "Postprocess.hpp"

#include <Windows.h>

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <string>
#include <system_error>

namespace SquadHeight
{
namespace
{
class MappedFloatFile
{
public:
    ~MappedFloatFile() { close(); }

    void close()
    {
        m_data = nullptr;
        if (m_view)
        {
            UnmapViewOfFile(m_view);
            m_view = nullptr;
        }
        if (m_mapping)
        {
            CloseHandle(m_mapping);
            m_mapping = nullptr;
        }
        if (m_file != INVALID_HANDLE_VALUE)
        {
            CloseHandle(m_file);
            m_file = INVALID_HANDLE_VALUE;
        }
    }

    bool open(const std::filesystem::path& path, std::uint64_t expected_floats, std::string& error)
    {
        m_file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (m_file == INVALID_HANDLE_VALUE)
        {
            error = "CreateFileW failed for raw raster, Win32=" + std::to_string(GetLastError());
            return false;
        }

        LARGE_INTEGER file_size{};
        if (!GetFileSizeEx(m_file, &file_size))
        {
            error = "GetFileSizeEx failed, Win32=" + std::to_string(GetLastError());
            return false;
        }

        const auto expected_bytes = expected_floats * sizeof(float);
        if (file_size.QuadPart < 0 || static_cast<std::uint64_t>(file_size.QuadPart) < expected_bytes)
        {
            error = "raw raster is smaller than rows*cols*4";
            return false;
        }

        m_mapping = CreateFileMappingW(m_file, nullptr, PAGE_READONLY, 0, 0, nullptr);
        if (!m_mapping)
        {
            error = "CreateFileMappingW failed, Win32=" + std::to_string(GetLastError());
            return false;
        }

        m_view = MapViewOfFile(m_mapping, FILE_MAP_READ, 0, 0, 0);
        if (!m_view)
        {
            error = "MapViewOfFile failed, Win32=" + std::to_string(GetLastError());
            return false;
        }
        m_data = static_cast<const float*>(m_view);
        return true;
    }

    float at(std::size_t row, std::size_t col, std::size_t cols) const
    {
        return m_data[row * cols + col];
    }

private:
    HANDLE m_file{INVALID_HANDLE_VALUE};
    HANDLE m_mapping{};
    void* m_view{};
    const float* m_data{};
};

std::pair<std::size_t, std::size_t> oriented_dimensions(std::size_t rows, std::size_t cols, const Snapshot& cfg)
{
    return cfg.transpose ? std::pair{cols, rows} : std::pair{rows, cols};
}

std::pair<std::size_t, std::size_t> oriented_to_source(
    std::size_t oriented_row,
    std::size_t oriented_col,
    std::size_t rows,
    std::size_t cols,
    const Snapshot& cfg)
{
    const auto [orows, ocols] = oriented_dimensions(rows, cols, cfg);
    std::size_t r = oriented_row;
    std::size_t c = oriented_col;
    if (cfg.flip_rows) r = orows - 1 - r;
    if (cfg.flip_cols) c = ocols - 1 - c;
    return cfg.transpose ? std::pair{c, r} : std::pair{r, c};
}

double normalized(float value, double z_offset)
{
    return std::isnan(value) ? 0.0 : static_cast<double>(value) - z_offset;
}

void append_fixed_2(std::string& out, double value)
{
    char buffer[64];
    auto result = std::to_chars(buffer, buffer + sizeof(buffer), value, std::chars_format::fixed, 2);
    if (result.ec == std::errc{})
    {
        out.append(buffer, result.ptr);
    }
    else
    {
        out += "0.00";
    }
}

std::string json_escape(std::string_view value)
{
    std::string out;
    out.reserve(value.size() + 8);
    for (const char raw_c : value)
    {
        const auto c = static_cast<unsigned char>(raw_c);
        switch (c)
        {
        case '\\': out += "\\\\"; break;
        case '"': out += "\\\""; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (c < 0x20)
            {
                char buf[7];
                std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned>(c));
                out += buf;
            }
            else
            {
                out.push_back(static_cast<char>(c));
            }
        }
    }
    return out;
}

std::string utc_now_iso8601()
{
    const auto now = std::chrono::system_clock::now();
    const auto t = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    gmtime_s(&tm, &t);
    char buffer[32];
    std::strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buffer;
}

bool write_height_json(const Snapshot& s, const MappedFloatFile& raw, const std::filesystem::path& path, int target_size, std::stop_token stop, std::string& error)
{
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out)
    {
        error = "could not open " + path.string();
        return false;
    }

    const auto [orows, ocols] = oriented_dimensions(s.rows, s.cols, s);
    const std::size_t out_rows = target_size > 0 ? static_cast<std::size_t>(target_size) : orows;
    const std::size_t out_cols = target_size > 0 ? static_cast<std::size_t>(target_size) : ocols;

    std::string row;
    row.reserve(out_cols * 10 + 8);
    out << "[\n";
    for (std::size_t r = 0; r < out_rows; ++r)
    {
        if (stop.stop_requested())
        {
            error = "post-process cancelled";
            return false;
        }
        if (r) out << ",\n";
        row.clear();
        row.push_back('[');

        const std::size_t orow = target_size > 0 ? std::min((r * orows) / out_rows, orows - 1) : r;
        for (std::size_t c = 0; c < out_cols; ++c)
        {
            if (c) row.push_back(',');
            const std::size_t ocol = target_size > 0 ? std::min((c * ocols) / out_cols, ocols - 1) : c;
            const auto [sr, sc] = oriented_to_source(orow, ocol, s.rows, s.cols, s);
            append_fixed_2(row, normalized(raw.at(sr, sc, s.cols), s.z_offset_m));
        }
        row.push_back(']');
        out.write(row.data(), static_cast<std::streamsize>(row.size()));
        if (!out)
        {
            error = "write failed for " + path.string();
            return false;
        }
    }
    out << "\n]\n";
    return static_cast<bool>(out);
}

bool write_meta(const Snapshot& s, std::string& error)
{
    std::ofstream out(s.meta_path, std::ios::binary | std::ios::trunc);
    if (!out)
    {
        error = "could not open " + s.meta_path.string();
        return false;
    }

    out.imbue(std::locale::classic());
    out << std::setprecision(10);
    out << "{\n";
    out << "  \"bounds_m\": {\"max_x\": " << s.bounds.max_x << ", \"max_y\": " << s.bounds.max_y
        << ", \"min_x\": " << s.bounds.min_x << ", \"min_y\": " << s.bounds.min_y << "},\n";
    out << "  \"exported_utc\": \"" << utc_now_iso8601() << "\",\n";
    out << "  \"grid_cols\": " << s.cols << ",\n";
    out << "  \"grid_rotation_deg\": " << s.rotation_deg << ",\n";
    out << "  \"grid_rows\": " << s.rows << ",\n";
    out << "  \"height_max_m\": " << (s.world_z_max_m - s.z_offset_m) << ",\n";
    out << "  \"height_min_m\": 0.0,\n";
    out << "  \"map\": \"" << json_escape(s.map_name) << "\",\n";
    out << "  \"orientation\": {\"flip_cols\": " << (s.flip_cols ? "true" : "false")
        << ", \"flip_rows\": " << (s.flip_rows ? "true" : "false")
        << ", \"transpose\": " << (s.transpose ? "true" : "false") << "},\n";
    out << "  \"raw\": {\n";
    out << "    \"file\": \"heightmap_world_f32.raw\",\n";
    out << "    \"format\": \"little-endian IEEE754 float32\",\n";
    out << "    \"layout\": \"row-major; source row=minY->maxY, col=minX->maxX before orientation\",\n";
    out << "    \"no_hit\": \"NaN; JSON writers replace with normalized 0 (map minimum)\",\n";
    out << "    \"units\": \"meters absolute UE world Z\"\n";
    out << "  },\n";
    out << "  \"resolution_m\": " << s.resolution_m << ",\n";
    out << "  \"stats\": {\n";
    out << "    \"cells\": " << s.stats.total_cells << ",\n";
    out << "    \"filtered_hits\": " << s.stats.filtered_hits << ",\n";
    out << "    \"max_hit_columns\": " << s.stats.max_hit_columns << ",\n";
    out << "    \"no_hit_cells\": " << s.stats.no_hit_cells << ",\n";
    out << "    \"overhang_drops\": " << s.stats.overhang_drops << ",\n";
    out << "    \"scan_seconds\": " << s.stats.scan_seconds << ",\n";
    out << "    \"structure_cells\": " << s.stats.structure_cells << ",\n";
    out << "    \"trace_calls\": " << s.stats.trace_calls << ",\n";
    out << "    \"vegetation_components_filtered\": " << s.stats.vegetation_components_filtered << "\n";
    out << "  },\n";
    out << "  \"surface_mode\": \"" << json_escape(s.surface_mode) << "\",\n";
    out << "  \"trace\": {\"bottom_m\": " << s.trace_bottom_m
        << ", \"fallback_simple_trace\": " << (s.fallback_simple_trace ? "true" : "false")
        << ", \"top_m\": " << s.trace_top_m
        << ", \"trace_complex\": " << (s.trace_complex ? "true" : "false")
        << ", \"trace_type_query\": " << s.trace_type_query << "},\n";
    out << "  \"world_name\": \"" << json_escape(s.world_name) << "\",\n";
    out << "  \"world_z_max_m\": " << s.world_z_max_m << ",\n";
    out << "  \"world_z_min_m\": " << s.world_z_min_m << ",\n";
    out << "  \"z_offset_m\": " << s.z_offset_m << "\n";
    out << "}\n";
    if (!out)
    {
        error = "write failed for " + s.meta_path.string();
        return false;
    }
    return true;
}
} // namespace

bool finalize_snapshot(const Snapshot& s, std::stop_token stop, const LogCallback& log, std::string& error)
{
    MappedFloatFile raw;
    if (!raw.open(s.raw_path, static_cast<std::uint64_t>(s.rows) * static_cast<std::uint64_t>(s.cols), error)) return false;

    log("Post-process: writing meta.json ...");
    if (!write_meta(s, error)) return false;

    if (s.downsample_to > 0)
    {
        log("Post-process: writing heightmap_" + std::to_string(s.downsample_to) + ".json ...");
        if (!write_height_json(s, raw, s.downsample_path, s.downsample_to, stop, error)) return false;
    }

    if (s.write_full_json)
    {
        log("Post-process: writing heightmap.json ...");
        if (!write_height_json(s, raw, s.full_json_path, 0, stop, error)) return false;
    }

    if (!s.keep_raw)
    {
        // Windows will not delete a file while our view/handles are still open.
        raw.close();
        std::error_code ec;
        std::filesystem::remove(s.raw_path, ec);
        if (ec) log("WARNING: could not delete raw raster: " + ec.message());
    }
    return true;
}
} // namespace SquadHeight
