cmake_minimum_required(VERSION 3.22)
set(TARGET SquadHeightRuntimeCpp)

add_library(${TARGET} SHARED
    src/dllmain.cpp
    src/SquadHeightRuntimeMod.cpp
    src/TraceInvoker.cpp
    src/Config.cpp
    src/Maps.cpp
    src/Postprocess.cpp
)

target_include_directories(${TARGET} PRIVATE "include")
target_link_libraries(${TARGET} PUBLIC UE4SS)
target_compile_features(${TARGET} PRIVATE cxx_std_23)

if(MSVC)
    target_compile_options(${TARGET} PRIVATE /W4 /permissive- /EHsc)
endif()

# Convenience package next to the build tree. UE4SS itself still decides where
# runtime binaries live; this only prepares the mod's own files.
set(PACKAGE_MOD_DIR "${CMAKE_BINARY_DIR}/package/Mods/${TARGET}")
add_custom_command(TARGET ${TARGET} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory "${PACKAGE_MOD_DIR}/dlls"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "$<TARGET_FILE:${TARGET}>" "${PACKAGE_MOD_DIR}/dlls/main.dll"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${CMAKE_CURRENT_SOURCE_DIR}/config.ini" "${PACKAGE_MOD_DIR}/config.ini"
    VERBATIM
)
