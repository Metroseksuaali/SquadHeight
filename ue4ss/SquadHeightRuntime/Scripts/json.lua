local M = {}

local function escape_string(s)
    s = s:gsub("\\", "\\\\")
         :gsub('"', '\\"')
         :gsub("\b", "\\b")
         :gsub("\f", "\\f")
         :gsub("\n", "\\n")
         :gsub("\r", "\\r")
         :gsub("\t", "\\t")
    return '"' .. s .. '"'
end

local function is_array(t)
    local max = 0
    local count = 0
    for k, _ in pairs(t) do
        if type(k) ~= "number" or k < 1 or k % 1 ~= 0 then
            return false, 0
        end
        if k > max then max = k end
        count = count + 1
    end
    if count == 0 then return true, 0 end
    return max == count, max
end

local function encode_value(v, pretty, indent)
    local tv = type(v)
    if tv == "nil" then
        return "null"
    elseif tv == "boolean" then
        return v and "true" or "false"
    elseif tv == "number" then
        if v ~= v or v == math.huge or v == -math.huge then
            return "null"
        end
        return string.format("%.10g", v):gsub(",", ".")
    elseif tv == "string" then
        return escape_string(v)
    elseif tv ~= "table" then
        return escape_string(tostring(v))
    end

    local arr, n = is_array(v)
    local next_indent = indent + 2
    local nl = pretty and "\n" or ""
    local sp = pretty and string.rep(" ", next_indent) or ""
    local close_sp = pretty and string.rep(" ", indent) or ""
    local sep = pretty and ",\n" or ","

    if arr then
        if n == 0 then return "[]" end
        local out = {"[", nl}
        for i = 1, n do
            if pretty then out[#out+1] = sp end
            out[#out+1] = encode_value(v[i], pretty, next_indent)
            if i < n then out[#out+1] = sep end
        end
        out[#out+1] = nl
        out[#out+1] = close_sp
        out[#out+1] = "]"
        return table.concat(out)
    end

    local keys = {}
    for k, _ in pairs(v) do keys[#keys+1] = tostring(k) end
    table.sort(keys)

    if #keys == 0 then return "{}" end
    local out = {"{", nl}
    for i, k in ipairs(keys) do
        if pretty then out[#out+1] = sp end
        out[#out+1] = escape_string(k)
        out[#out+1] = pretty and ": " or ":"
        out[#out+1] = encode_value(v[k], pretty, next_indent)
        if i < #keys then out[#out+1] = sep end
    end
    out[#out+1] = nl
    out[#out+1] = close_sp
    out[#out+1] = "}"
    return table.concat(out)
end

function M.encode(v, pretty)
    return encode_value(v, pretty == true, 0)
end

return M
