#ifndef HHI_GTT_H
#define HHI_GTT_H

#include "Gtt4x4.h"

struct GttMode
{
  int subExperiment, size;
  uint8_t sizeMask, groupMode;
};

const GttMode &getGttMode(int subExperiment);

#if HHI_GTT_COMMON
#include <vector>

constexpr uint8_t GTT_VERSION = 3;
constexpr int GTT_MAX_COEFF = 32 * 32;
using GttPath = Gtt4x4Path;


struct GttLayout
{
  int width = 0, height = 0, numCoeff = 0;
  std::array<int, 4> groupBegin {}, groupEnd {};
  int numCg = 0, cgSize = 16;
  uint8_t groupMode = 0;
};

struct GttConstraints
{
  int minLastScan = 0, maxLastScan = -1;
  std::array<int8_t, 4> requiredParity {{ -1, -1, -1, -1 }};
};

struct GttQuantResult
{
  bool feasible = false;
  std::vector<TCoeff> qRaster;
  int lastScan = -1;
  int64_t absSum = 0, proxyCost = 0;
};

GttLayout makeGttLayout(int size, uint8_t groupMode);
GttLayout getGttLayout(const TransformUnit &tu);
GttPath deriveGttPath(const CCoeffBuf &finalLevels, const GttLayout &layout);
void forwardGttPath(const CCoeffBuf &baseRaster, GttPath path, CoeffBuf &pathRaster, const GttLayout &layout);
void inverseGttPath(const CCoeffBuf &dequantRaster, GttPath path, CoeffBuf &baseRaster, const GttLayout &layout);

#endif
#endif
