#include "Gtt.h"

const GttMode &getGttMode(int subExperiment)
{
  static constexpr GttMode modes[] = {
    { 0, 0, 0x00, 0 }, { 1, 8, 0x02, 0 }, { 2, 16, 0x04, 0 }, { 3, 32, 0x08, 0 },
    { 4, 16, 0x04, 1 }, { 5, 32, 0x08, 1 }, { 6, 4, 0x01, 0 }
  };
  CHECK(subExperiment < 0 || subExperiment > 6, "Unregistered GTT subexperiment");
  return modes[subExperiment];
}

#if HHI_GTT_COMMON
#include "CodingStructure.h"
#include "Rom.h"
#include "Slice.h"
#include <limits>

namespace
{
const ScanElement *scanFor(const GttLayout &layout)
{
  const auto canonical = makeGttLayout(layout.width, layout.groupMode);
  CHECK(layout.height != canonical.height || layout.numCoeff != canonical.numCoeff ||
          layout.groupBegin != canonical.groupBegin || layout.groupEnd != canonical.groupEnd ||
          layout.numCg != canonical.numCg || layout.cgSize != canonical.cgSize,
        "Invalid GTT layout");
  CHECK(!gp_sizeIdxInfo, "GTT requires initialized transform scan tables");
  const auto *scan = g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG]
                               [gp_sizeIdxInfo->idxFrom(layout.width)][gp_sizeIdxInfo->idxFrom(layout.height)];
  CHECK(!scan, "Missing GTT scan table");
  return scan;
}

template<typename T> void checkBlock(const AreaBuf<T> &buf, const GttLayout &layout)
{
  CHECK(!buf.buf || buf.width != layout.width || buf.height != layout.height || buf.stride < layout.width,
        "GTT requires a complete transform buffer");
}

TCoeff roundQ10(int64_t value)
{
  const int64_t rounded = value >= 0 ? (value + 512) / 1024 : -((-value + 512) / 1024);
  CHECK(rounded < std::numeric_limits<TCoeff>::min() || rounded > std::numeric_limits<TCoeff>::max(),
        "GTT rotation exceeds TCoeff range");
  return TCoeff(rounded);
}

void rotate(std::array<TCoeff, GTT_MAX_COEFF> &values, int first, int second, int length, bool inverse)
{
  for (int i = 0; i < length; ++i)
  {
    const int64_t a = values[first + i], b = values[second + i];
    values[first + i] = roundQ10(724LL * (inverse ? a - b : a + b));
    values[second + i] = roundQ10(724LL * (inverse ? a + b : b - a));
  }
}

void applyPath(const CCoeffBuf &src, GttPath path, CoeffBuf &dst, const GttLayout &layout, bool inverse)
{
  CHECK(path.k2 > 1 || path.k3 > 1, "Invalid GTT kernel path");
  const auto *scan = scanFor(layout);
  checkBlock(src, layout);
  checkBlock(dst, layout);
  std::array<TCoeff, GTT_MAX_COEFF> values;
  for (int i = 0; i < layout.numCoeff; ++i)
  {
    const int raster = scan[i].idx;
    values[i] = src.buf[(raster / layout.width) * src.stride + raster % layout.width];
  }
  const int length = layout.groupEnd[1] - layout.groupBegin[1];
  if (inverse && path.k3) { rotate(values, layout.groupBegin[2], layout.groupBegin[3], length, true); }
  if (path.k2) { rotate(values, layout.groupBegin[1], layout.groupBegin[2], length, inverse); }
  if (!inverse && path.k3) { rotate(values, layout.groupBegin[2], layout.groupBegin[3], length, false); }
  for (int i = 0; i < layout.numCoeff; ++i)
  {
    const int raster = scan[i].idx;
    dst.buf[(raster / layout.width) * dst.stride + raster % layout.width] = values[i];
  }
}
}

GttLayout makeGttLayout(int size, uint8_t groupMode)
{
  CHECK(size != 4 && size != 8 && size != 16 && size != 32, "Unsupported GTT transform size");
  CHECK(groupMode > 1 || (groupMode && size == 4), "Unsupported GTT grouping");
  GttLayout layout;
  layout.width = layout.height = size;
  layout.numCoeff = size * size;
  layout.numCg = layout.numCoeff / layout.cgSize;
  layout.groupMode = groupMode;
  const int firstLength = groupMode ? 16 : layout.numCoeff / 4;
  const int tailLength = (layout.numCoeff - firstLength) / 3;
  for (int group = 0; group < 4; ++group)
  {
    layout.groupBegin[group] = group ? layout.groupEnd[group - 1] : 0;
    layout.groupEnd[group] = layout.groupBegin[group] + (group ? tailLength : firstLength);
  }
  return layout;
}

GttLayout getGttLayout(const TransformUnit &tu)
{
  CHECK(!tu.cs || !tu.cs->sps || !tu.Y().valid() || tu.Y().width != tu.Y().height,
        "Invalid GTT transform unit");
  const auto &sps = *tu.cs->sps;
  CHECK(!sps.m_gttEnabled, "GTT layout requested while tool is disabled");
  CHECK(sps.m_gttVersion != GTT4X4_VERSION && sps.m_gttVersion != GTT_VERSION, "Unknown GTT syntax version");
  if (sps.m_gttVersion == GTT4X4_VERSION)
  {
    CHECK(tu.Y().width != 4 || sps.m_gttSizeMask != 1 || sps.m_gttGroupMode != 0,
          "Invalid legacy GTT4x4 layout");
    return makeGttLayout(4, 0);
  }
  const auto layout = makeGttLayout(tu.Y().width, sps.m_gttGroupMode);
  CHECK(!(sps.m_gttSizeMask & (1 << (floorLog2(layout.width) - 2))), "GTT size is not enabled in SPS");
  return layout;
}

GttPath deriveGttPath(const CCoeffBuf &finalLevels, const GttLayout &layout)
{
  const auto *scan = scanFor(layout);
  checkBlock(finalLevels, layout);
  uint8_t parity[2] {};
  for (int group = 0; group < 2; ++group)
  {
    for (int i = layout.groupBegin[group]; i < layout.groupEnd[group]; ++i)
    {
      const int raster = scan[i].idx;
      const int64_t level = finalLevels.buf[(raster / layout.width) * finalLevels.stride + raster % layout.width];
      parity[group] ^= uint8_t(uint64_t(level < 0 ? -level : level) & 1);
    }
  }
  return { parity[0], parity[1] };
}

void forwardGttPath(const CCoeffBuf &baseRaster, GttPath path, CoeffBuf &pathRaster, const GttLayout &layout)
{
  applyPath(baseRaster, path, pathRaster, layout, false);
}

void inverseGttPath(const CCoeffBuf &dequantRaster, GttPath path, CoeffBuf &baseRaster, const GttLayout &layout)
{
  applyPath(dequantRaster, path, baseRaster, layout, true);
}
#endif
