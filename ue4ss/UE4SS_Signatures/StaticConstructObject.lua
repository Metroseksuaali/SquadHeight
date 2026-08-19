function Register()
    return "48 89 5C 24 10 48 89 74 24 18 48 89 7C 24 20 55 41 54 41 55 41 56 41 57 48 8D AC 24 30 FE FF FF 48 81 EC D0 02 00 00 48 8B 05 ?? ?? ?? ?? 48 33 C4 48 89 85 C0 01 00 00 48 8B 71 28 33 DB 44 8B 79 70 48 8B F9 4C 8B 31 4C 8B 69 08"
end

function OnMatchFound(MatchAddress)
    return MatchAddress
end
