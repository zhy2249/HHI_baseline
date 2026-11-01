foreach( suite Common Quant Syntax Search )
  add_executable( Gtt${suite}Tests Gtt${suite}Tests.cpp )
  target_link_libraries( Gtt${suite}Tests PRIVATE EncoderLib DecoderLib CommonLib Threads::Threads )
  add_test( NAME Gtt${suite} COMMAND Gtt${suite}Tests )
  set_tests_properties( Gtt${suite} PROPERTIES SKIP_RETURN_CODE 77 )
endforeach()
add_custom_target( GttUnitTests DEPENDS GttCommonTests GttQuantTests GttSyntaxTests GttSearchTests )

# This executable only constructs the configuration parser, never an encoder.
add_executable( GttConfigProbe ConfigProbe.cpp ../../source/App/EncoderApp/EncAppCfg.cpp )
target_include_directories( GttConfigProbe PRIVATE ../../source/App/EncoderApp )
target_link_libraries( GttConfigProbe PRIVATE EncoderLib DecoderLib Utilities CommonLib Threads::Threads )
add_dependencies( GttUnitTests GttConfigProbe )
find_package( Python3 REQUIRED COMPONENTS Interpreter )
add_test( NAME GttConfig COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/ConfigTests.py
  --probe $<TARGET_FILE:GttConfigProbe> --repo-root ${CMAKE_SOURCE_DIR} )
