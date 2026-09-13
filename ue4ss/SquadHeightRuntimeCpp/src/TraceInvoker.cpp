#include "TraceInvoker.hpp"

#include <cstring>
#include <exception>

#include <Helpers/String.hpp>

using namespace RC;
using namespace RC::Unreal;

namespace SquadHeight
{
namespace
{
FProperty* require_property(UStruct* owner, const wchar_t* name, std::string& error)
{
    if (!owner)
    {
        error = "reflection owner is null";
        return nullptr;
    }
    auto* property = owner->GetPropertyByNameInChain(name);
    if (!property) error = "missing reflected property: " + RC::to_string(name);
    return property;
}

FStructProperty* require_struct(UStruct* owner, const wchar_t* name, std::string& error)
{
    auto* property = require_property(owner, name, error);
    if (!property) return nullptr;
    if (!property->IsA<FStructProperty>())
    {
        error = "property is not StructProperty: " + RC::to_string(name);
        return nullptr;
    }
    return static_cast<FStructProperty*>(property);
}
} // namespace

TraceInvoker::NumberKind TraceInvoker::number_kind(FProperty* property)
{
    if (!property) return NumberKind::Invalid;
    if (property->IsA<Unreal::FDoubleProperty>()) return NumberKind::Float64;
    if (property->IsA<FFloatProperty>()) return NumberKind::Float32;
    if (property->IsA<FIntProperty>()) return NumberKind::Int32;
    return NumberKind::Invalid;
}

bool TraceInvoker::cache_number(FProperty* property, NumberField& out, std::string& error)
{
    out = {};
    if (!property)
    {
        error = "number property is null";
        return false;
    }
    out.offset = property->GetOffset_Internal();
    out.kind = number_kind(property);
    if (out.kind == NumberKind::Invalid)
    {
        error = "unsupported numeric property type";
        return false;
    }
    return true;
}

bool TraceInvoker::cache_vector_layout(UStruct* owner, const wchar_t* name, VectorLayout& out, bool required, std::string& error)
{
    out = {};
    auto* property = owner ? owner->GetPropertyByNameInChain(name) : nullptr;
    if (!property)
    {
        if (required) error = "missing vector property: " + RC::to_string(name);
        return !required;
    }
    if (!property->IsA<FStructProperty>())
    {
        if (required) error = "vector property is not StructProperty: " + RC::to_string(name);
        return !required;
    }

    auto* struct_property = static_cast<FStructProperty*>(property);
    auto* vector_struct = struct_property->GetStruct().Get();
    if (!vector_struct)
    {
        if (required) error = "vector struct metadata is null: " + RC::to_string(name);
        return !required;
    }

    auto* x = vector_struct->GetPropertyByNameInChain(STR("X"));
    auto* y = vector_struct->GetPropertyByNameInChain(STR("Y"));
    auto* z = vector_struct->GetPropertyByNameInChain(STR("Z"));
    if (!x || !y || !z)
    {
        if (required) error = "vector struct has no X/Y/Z fields: " + RC::to_string(name);
        return !required;
    }

    VectorLayout candidate{};
    candidate.offset = property->GetOffset_Internal();
    std::string field_error;
    if (!cache_number(x, candidate.x, field_error) || !cache_number(y, candidate.y, field_error) || !cache_number(z, candidate.z, field_error))
    {
        if (required) error = "unsupported vector field in " + RC::to_string(name) + ": " + field_error;
        return !required;
    }
    candidate.valid = true;
    out = candidate;
    return true;
}

bool TraceInvoker::cache_bool_layout(UStruct* owner, const wchar_t* name, BoolLayout& out, std::string& error)
{
    out = {};
    auto* property = require_property(owner, name, error);
    if (!property) return false;
    if (!property->IsA<FBoolProperty>())
    {
        error = "property is not BoolProperty: " + RC::to_string(name);
        return false;
    }
    auto* bool_property = static_cast<FBoolProperty*>(property);
    out.offset = property->GetOffset_Internal() + static_cast<std::int32_t>(bool_property->GetByteOffset());
    out.mask = bool_property->GetFieldMask();
    out.valid = true;
    return true;
}

bool TraceInvoker::cache_integer_layout(UStruct* owner, const wchar_t* name, IntegerLayout& out, std::string& error)
{
    out = {};
    auto* property = require_property(owner, name, error);
    if (!property) return false;
    const auto size = property->GetElementSize();
    if (size != 1 && size != 2 && size != 4 && size != 8)
    {
        error = "unsupported integer/enum storage size for " + RC::to_string(name) + ": " + std::to_string(size);
        return false;
    }
    out.offset = property->GetOffset_Internal();
    out.size = size;
    out.valid = true;
    return true;
}

std::uint8_t* TraceInvoker::address(void* base, std::int32_t offset)
{
    return static_cast<std::uint8_t*>(base) + offset;
}

const std::uint8_t* TraceInvoker::address(const void* base, std::int32_t offset)
{
    return static_cast<const std::uint8_t*>(base) + offset;
}

void TraceInvoker::write_number(const NumberField& field, void* container, double value)
{
    if (!container || field.kind == NumberKind::Invalid) return;
    auto* dst = address(container, field.offset);
    switch (field.kind)
    {
    case NumberKind::Float64:
        std::memcpy(dst, &value, sizeof(value));
        break;
    case NumberKind::Float32:
    {
        const float v = static_cast<float>(value);
        std::memcpy(dst, &v, sizeof(v));
        break;
    }
    case NumberKind::Int32:
    {
        const std::int32_t v = static_cast<std::int32_t>(value);
        std::memcpy(dst, &v, sizeof(v));
        break;
    }
    case NumberKind::Invalid:
        break;
    }
}

double TraceInvoker::read_number(const NumberField& field, const void* container, bool& ok)
{
    ok = false;
    if (!container || field.kind == NumberKind::Invalid) return 0.0;
    const auto* src = address(container, field.offset);
    switch (field.kind)
    {
    case NumberKind::Float64:
    {
        double v{};
        std::memcpy(&v, src, sizeof(v));
        ok = true;
        return v;
    }
    case NumberKind::Float32:
    {
        float v{};
        std::memcpy(&v, src, sizeof(v));
        ok = true;
        return static_cast<double>(v);
    }
    case NumberKind::Int32:
    {
        std::int32_t v{};
        std::memcpy(&v, src, sizeof(v));
        ok = true;
        return static_cast<double>(v);
    }
    case NumberKind::Invalid:
        return 0.0;
    }
    return 0.0;
}

void TraceInvoker::write_bool(const BoolLayout& field, void* container, bool value)
{
    if (!field.valid || !container) return;
    auto* byte_value = address(container, field.offset);
    if (field.mask == 0xFF)
    {
        *byte_value = value ? 1 : 0;
    }
    else if (value)
    {
        *byte_value |= field.mask;
    }
    else
    {
        *byte_value &= static_cast<std::uint8_t>(~field.mask);
    }
}

bool TraceInvoker::read_bool(const BoolLayout& field, const void* container)
{
    if (!field.valid || !container) return false;
    const auto byte_value = *address(container, field.offset);
    return field.mask == 0xFF ? (byte_value != 0) : ((byte_value & field.mask) != 0);
}

void TraceInvoker::write_integer(const IntegerLayout& field, void* container, std::uint64_t value)
{
    if (!field.valid || !container) return;
    auto* dst = address(container, field.offset);
    switch (field.size)
    {
    case 1:
    {
        const auto v = static_cast<std::uint8_t>(value);
        std::memcpy(dst, &v, sizeof(v));
        break;
    }
    case 2:
    {
        const auto v = static_cast<std::uint16_t>(value);
        std::memcpy(dst, &v, sizeof(v));
        break;
    }
    case 4:
    {
        const auto v = static_cast<std::uint32_t>(value);
        std::memcpy(dst, &v, sizeof(v));
        break;
    }
    case 8:
        std::memcpy(dst, &value, sizeof(value));
        break;
    default:
        break;
    }
}

UObject* TraceInvoker::read_weak_object(const WeakObjectLayout& field, void* container)
{
    if (!field.valid || !container) return nullptr;
    auto* weak = reinterpret_cast<FWeakObjectPtr*>(address(container, field.offset));
    return weak->Get();
}

void TraceInvoker::write_vector(const VectorLayout& layout, void* root_container, double x, double y, double z)
{
    if (!layout.valid || !root_container) return;
    void* vector_data = address(root_container, layout.offset);
    write_number(layout.x, vector_data, x);
    write_number(layout.y, vector_data, y);
    write_number(layout.z, vector_data, z);
}

double TraceInvoker::read_vector_z(const VectorLayout& layout, const void* root_container, bool& ok)
{
    ok = false;
    if (!layout.valid || !root_container) return 0.0;
    const void* vector_data = address(root_container, layout.offset);
    return read_number(layout.z, vector_data, ok);
}

bool TraceInvoker::initialize(std::string& error)
{
    try
    {
        m_ksl = UObjectGlobals::StaticFindObject<UObject*>(nullptr, nullptr, STR("/Script/Engine.Default__KismetSystemLibrary"));
        if (!m_ksl)
        {
            error = "Default__KismetSystemLibrary was not found";
            return false;
        }

        m_function = m_ksl->GetFunctionByNameInChain(STR("LineTraceSingle"));
        if (!m_function)
        {
            error = "KismetSystemLibrary.LineTraceSingle was not found";
            return false;
        }

        auto* world_context = require_property(m_function, STR("WorldContextObject"), error);
        if (!world_context) return false;
        m_world_context_offset = world_context->GetOffset_Internal();

        if (!cache_vector_layout(m_function, STR("Start"), m_start, true, error)) return false;
        if (!cache_vector_layout(m_function, STR("End"), m_end, true, error)) return false;
        if (!cache_integer_layout(m_function, STR("TraceChannel"), m_trace_channel, error)) return false;
        if (!cache_bool_layout(m_function, STR("bTraceComplex"), m_trace_complex, error)) return false;
        if (!cache_bool_layout(m_function, STR("bIgnoreSelf"), m_ignore_self, error)) return false;
        if (!cache_bool_layout(m_function, STR("ReturnValue"), m_return_value, error)) return false;

        auto* out_hit = require_struct(m_function, STR("OutHit"), error);
        if (!out_hit) return false;
        m_out_hit_offset = out_hit->GetOffset_Internal();
        m_out_hit_size = out_hit->GetElementSize();
        auto* hit_struct = out_hit->GetStruct().Get();
        if (!hit_struct)
        {
            error = "FHitResult metadata is null";
            return false;
        }

        // UE 5.7 uses FVector_NetQuantize / FVector_NetQuantizeNormal. We only
        // cache reflected offsets and numeric storage types; no hard-coded UE struct
        // offsets are compiled into the mod.
        cache_vector_layout(hit_struct, STR("ImpactPoint"), m_hit_impact_point, false, error);
        cache_vector_layout(hit_struct, STR("Location"), m_hit_location, false, error);
        if (!m_hit_impact_point.valid && !m_hit_location.valid)
        {
            error = "FHitResult has neither readable ImpactPoint nor Location";
            return false;
        }
        cache_vector_layout(hit_struct, STR("ImpactNormal"), m_hit_impact_normal, false, error);
        cache_vector_layout(hit_struct, STR("Normal"), m_hit_normal, false, error);

        auto* component = hit_struct->GetPropertyByNameInChain(STR("Component"));
        if (!component || !component->IsA<FWeakObjectProperty>())
        {
            error = "FHitResult.Component is missing or is not WeakObjectProperty";
            return false;
        }
        m_hit_component = {component->GetOffset_Internal(), true};

        m_hit_object_handle_offset = -1;
        m_hit_reference_object = {};
        if (auto* handle_prop = hit_struct->GetPropertyByNameInChain(STR("HitObjectHandle")); handle_prop && handle_prop->IsA<FStructProperty>())
        {
            auto* handle_struct_property = static_cast<FStructProperty*>(handle_prop);
            if (auto* handle_struct = handle_struct_property->GetStruct().Get())
            {
                auto* reference = handle_struct->GetPropertyByNameInChain(STR("ReferenceObject"));
                if (reference && reference->IsA<FWeakObjectProperty>())
                {
                    m_hit_object_handle_offset = handle_prop->GetOffset_Internal();
                    m_hit_reference_object = {reference->GetOffset_Internal(), true};
                }
            }
        }

        m_hit_time = {};
        if (auto* time = hit_struct->GetPropertyByNameInChain(STR("Time")))
        {
            std::string ignored;
            cache_number(time, m_hit_time, ignored);
        }

        const auto params_size = static_cast<std::size_t>(m_function->GetParmsSize());
        if (params_size == 0)
        {
            error = "LineTraceSingle parameter block has size 0";
            return false;
        }
        m_params.assign(params_size, 0);
        return true;
    }
    catch (const std::exception& e)
    {
        error = std::string("trace reflection initialization exception: ") + e.what();
        return false;
    }
}

bool TraceInvoker::configure(UObject* world_context, int trace_type_query, std::string& error)
{
    if (!m_function || m_params.empty() || m_world_context_offset < 0)
    {
        error = "TraceInvoker is not initialized";
        return false;
    }
    if (!world_context)
    {
        error = "world context is null";
        return false;
    }

    std::memset(m_params.data(), 0, m_params.size());
    std::memcpy(address(m_params.data(), m_world_context_offset), &world_context, sizeof(world_context));
    write_integer(m_trace_channel, m_params.data(), static_cast<std::uint64_t>(trace_type_query));
    write_bool(m_ignore_self, m_params.data(), true);
    return true;
}

bool TraceInvoker::trace(double x_cm, double y_cm, double z_start_cm, double z_end_cm, bool trace_complex, TraceHit& out, std::string& error)
{
    error.clear();
    if (!m_function || !m_ksl || m_params.empty() || m_out_hit_offset < 0)
    {
        error = "TraceInvoker is not configured";
        return false;
    }

    try
    {
        void* out_hit_data = address(m_params.data(), m_out_hit_offset);
        std::memset(out_hit_data, 0, static_cast<std::size_t>(m_out_hit_size));
        if (m_hit_time.kind != NumberKind::Invalid) write_number(m_hit_time, out_hit_data, 1.0);

        write_vector(m_start, m_params.data(), x_cm, y_cm, z_start_cm);
        write_vector(m_end, m_params.data(), x_cm, y_cm, z_end_cm);
        write_bool(m_trace_complex, m_params.data(), trace_complex);
        write_bool(m_return_value, m_params.data(), false);

        m_ksl->ProcessEvent(m_function, m_params.data());
        if (!read_bool(m_return_value, m_params.data())) return false;

        bool ok = false;
        double z = read_vector_z(m_hit_impact_point, out_hit_data, ok);
        if (!ok) z = read_vector_z(m_hit_location, out_hit_data, ok);
        if (!ok)
        {
            error = "could not read FHitResult impact Z";
            return false;
        }

        double normal_z = 1.0;
        bool normal_ok = false;
        normal_z = read_vector_z(m_hit_impact_normal, out_hit_data, normal_ok);
        if (!normal_ok) normal_z = read_vector_z(m_hit_normal, out_hit_data, normal_ok);
        if (!normal_ok) normal_z = 1.0;

        out = {};
        out.z_cm = z;
        out.normal_z = normal_z;
        out.component = read_weak_object(m_hit_component, out_hit_data);

        if (m_hit_object_handle_offset >= 0 && m_hit_reference_object.valid)
        {
            void* handle_data = address(out_hit_data, m_hit_object_handle_offset);
            out.reference_object = read_weak_object(m_hit_reference_object, handle_data);
        }
        return true;
    }
    catch (const std::exception& e)
    {
        error = std::string("LineTraceSingle ProcessEvent exception: ") + e.what();
        return false;
    }
}
} // namespace SquadHeight
