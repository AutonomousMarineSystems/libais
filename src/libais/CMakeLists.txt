
add_library(ais 
ais.cpp
ais1_2_3.cpp
ais4_11.cpp
ais5.cpp
ais6.cpp
ais7_13.cpp
ais8.cpp
ais8_1_22.cpp
ais8_1_26.cpp
ais8_200.cpp
ais8_366.cpp
ais8_366_22.cpp
ais8_367.cpp
ais9.cpp
ais10.cpp
ais12.cpp
ais14.cpp
ais15.cpp
ais16.cpp
ais17.cpp
ais18.cpp
ais19.cpp
ais20.cpp
ais21.cpp
ais22.cpp
ais23.cpp
ais24.cpp
ais25.cpp
ais26.cpp
ais27.cpp
vdm.cpp
decode_body.cpp
)

install(TARGETS ais LIBRARY DESTINATION lib)

file(GLOB HEADERS *.h)
install(FILES ${HEADERS} DESTINATION include/libais)

set(PACKAGE_VERSION 0.15+)
set_target_properties(ais PROPERTIES VERSION ${PACKAGE_VERSION} SOVERSION 1)


# Not yet handled:
# ais_py.cpp
# ais_decode_normed.cpp 
