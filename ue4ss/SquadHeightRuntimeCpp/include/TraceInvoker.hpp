#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include <UnrealDef.hpp>

namespace SquadHeight
{
struct TraceHit
{
    double z_cm{};
    double normal_z{1.0};
    RC::UObject* component{};
    RC::UObject* reference_object{};
};

class TraceInvoker
{
public:
    bool initialize(std::string& error);
    bool configure(RC::UObject* world_context, int trace_type_query, std::string& error);
    bool trace(double x_cm, double y_cm, double z_start_cm, double z_end_cm, bool trace_complex, TraceHit& out, std::string& error);

private:
    enum class NumberKind : std::uint8_t
    {
        Invalid,
        Float32,
        Float64,
        Int32,
    };

    struct NumberField
    {
        std::int32_t offset{};
        NumberKind kind{NumberKind::Invalid};
    };

    struct VectorLayout
    {
        std::int32_t offset{};
        NumberField x{};
        NumberField y{};
        NumberField z{};
        bool valid{};
    };

    struct BoolLayout
    {
        std::int32_t offset{};
        std::uint8_t mask{0xFF};
        bool valid{};
    };

    struct IntegerLayout
    {
        std::int32_t offset{};
        std::int32_t size{};
        bool valid{};
    };

    struct WeakObjectLayout
    {
        std::int32_t offset{};
        bool valid{};
    };

    RC::UObject* m_ksl{};
    RC::UFunction* m_function{};
    std::vector<std::uint8_t> m_params;

    std::int32_t m_world_context_offset{-1};
    VectorLayout m_start{};
    VectorLayout m_end{};
    IntegerLayout m_trace_channel{};
    BoolLayout m_trace_complex{};
    std::int32_t m_out_hit_offset{-1};
    std::int32_t m_out_hit_size{};
    BoolLayout m_ignore_self{};
    BoolLayout m_return_value{};

    VectorLayout m_hit_impact_point{};
    VectorLayout m_hit_location{};
    VectorLayout m_hit_impact_normal{};
    VectorLayout m_hit_normal{};
    WeakObjectLayout m_hit_component{};
    std::int32_t m_hit_object_handle_offset{-1};
    WeakObjectLayout m_hit_reference_object{};
    NumberField m_hit_time{};

    static NumberKind number_kind(RC::FProperty* property);
    static bool cache_number(RC::FProperty* property, NumberField& out, std::string& error);
    static bool cache_vector_layout(RC::UStruct* owner, const wchar_t* name, VectorLayout& out, bool required, std::string& error);
    static bool cache_bool_layout(RC::UStruct* owner, const wchar_t* name, BoolLayout& out, std::string& error);
    static bool cache_integer_layout(RC::UStruct* owner, const wchar_t* name, IntegerLayout& out, std::string& error);

    static std::uint8_t* address(void* base, std::int32_t offset);
    static const std::uint8_t* address(const void* base, std::int32_t offset);
    static void write_number(const NumberField& field, void* container, double value);
    static double read_number(const NumberField& field, const void* container, bool& ok);
    static void write_bool(const BoolLayout& field, void* container, bool value);
    static bool read_bool(const BoolLayout& field, const void* container);
    static void write_integer(const IntegerLayout& field, void* container, std::uint64_t value);
    static RC::UObject* read_weak_object(const WeakObjectLayout& field, void* container);
    static void write_vector(const VectorLayout& layout, void* root_container, double x, double y, double z);
    static double read_vector_z(const VectorLayout& layout, const void* root_container, bool& ok);
};
} // namespace SquadHeight
