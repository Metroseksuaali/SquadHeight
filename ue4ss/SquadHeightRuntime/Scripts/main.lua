local Config = require("config")
local Exporter = require("exporter")

print("[SquadHeightRuntime] Loaded. F8 starts/stops the current-map height export.\n")

local hotkey = Key[Config.hotkey]
if hotkey == nil then
    error("[SquadHeightRuntime] Unknown hotkey in config.lua: " .. tostring(Config.hotkey))
end

RegisterKeyBind(hotkey, function()
    ExecuteInGameThread(function()
        Exporter.toggle()
    end)
end)
