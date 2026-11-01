#ifndef GTT4X4_H
#define GTT4X4_H

#include "CommonDef.h"

#if HHI_GTT_COMMON
#include "Unit.h"
#include "Buffer.h"
#include <array>

// Version 2 uses a context-coded TU flag. Version 1 was the EP draft.
constexpr uint8_t GTT4X4_VERSION = 2;

struct Gtt4x4Path
{
  uint8_t k2 = 0;
  uint8_t k3 = 0;
};

struct Gtt4x4Constraints
{
  uint8_t minLastScan = 0;
  uint8_t maxLastScan = 3;
  std::array<int8_t, 4> requiredParity {{ -1, -1, -1, -1 }};
};

struct Gtt4x4QuantResult
{
  bool feasible = false;
  std::array<TCoeff, 16> qRaster {};
  TCoeff absSum = 0;
  int lastScan = -1;
  int64_t proxyCost = 0;
};

Gtt4x4Path deriveGtt4x4Path(const CCoeffBuf &finalLevels);
void forwardGtt4x4Path(const CCoeffBuf &baseRaster, Gtt4x4Path path, CoeffBuf &pathRaster);
void inverseGtt4x4Path(const CCoeffBuf &dequantRaster, Gtt4x4Path path, CoeffBuf &baseRaster);

#endif
#endif
