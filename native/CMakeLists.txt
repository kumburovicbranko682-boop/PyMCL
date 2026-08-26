cmake_minimum_required(VERSION 3.16)
project(pymcl_bridge C)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(-Wall -Wno-unused-parameter -Wno-unused-function)
add_compile_definitions(UNICODE _UNICODE WIN32_LEAN_AND_MEAN)

include_directories(${CMAKE_SOURCE_DIR}/include ${CMAKE_SOURCE_DIR}/vendor)

file(GLOB SRC src/*.c)
add_executable(pymcl-bridge
  ${SRC}
  vendor/cJSON.c
)

target_link_libraries(pymcl-bridge PRIVATE
  curl
  z
  bcrypt
  crypt32
  ws2_32
  winhttp
  pthread
  ole32
  shell32
  advapi32
  user32
)

set_target_properties(pymcl-bridge PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/build
  RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_SOURCE_DIR}/build
  RUNTIME_OUTPUT_DIRECTORY_DEBUG ${CMAKE_SOURCE_DIR}/build
)

if(EXISTS "C:/msys64/mingw64/etc/ssl/certs/ca-bundle.crt")
  add_custom_command(TARGET pymcl-bridge POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      "C:/msys64/mingw64/etc/ssl/certs/ca-bundle.crt"
      "$<TARGET_FILE_DIR:pymcl-bridge>/curl-ca-bundle.crt")
endif()

set(_mingw_bin "C:/msys64/mingw64/bin")
foreach(_dll
    libcurl-4.dll libssl-3-x64.dll libcrypto-3-x64.dll zlib1.dll
    libzstd.dll libbrotlidec.dll libbrotlicommon.dll libnghttp2-14.dll
    libidn2-0.dll libpsl-5.dll libssh2-1.dll libiconv-2.dll libintl-8.dll
    libunistring-2.dll libgcc_s_seh-1.dll libwinpthread-1.dll
    libstdc++-6.dll)
  if(EXISTS "${_mingw_bin}/${_dll}")
    add_custom_command(TARGET pymcl-bridge POST_BUILD
      COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${_mingw_bin}/${_dll}" "$<TARGET_FILE_DIR:pymcl-bridge>/${_dll}")
  endif()
endforeach()
