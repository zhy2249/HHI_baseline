#include "Gtt4x4.h"

#if HHI_GTT4X4
#include "Rom.h"
#include <limits>

namespace
{
const ScanElement *gttScan()
{
  CHECK(!gp_sizeIdxInfo, "GTT4x4 requires initialized transform scan tables");
  const auto *scan = g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG]
                               [gp_sizeIdxInfo->idxFrom(4)][gp_sizeIdxInfo->idxFrom(4)];
  CHECK(!scan, "GTT4x4 scan table is missing");
  return scan;
}

template<typename T> void checkBlock(const AreaBuf<T> &buf)
{
  CHECK(!buf.buf || buf.width != 4 || buf.height != 4 || buf.stride < 4, "GTT4x4 requires a full 4x4 buffer");
}

std::array<TCoeff, 16> gather(const CCoeffBuf &buf)
{
  checkBlock(buf);
  const auto *scan = gttScan();
  std::array<TCoeff, 16> values {};
  for (unsigned i = 0; i < values.size(); ++i)
  {
    values[i] = buf.buf[(scan[i].idx / 4) * buf.stride + scan[i].idx % 4];
  }
  return values;
}

void scatter(const std::array<TCoeff, 16> &values, CoeffBuf &buf)
{
  checkBlock(buf);
  const auto *scan = gttScan();
  for (unsigned i = 0; i < values.size(); ++i)
  {
    buf.buf[(scan[i].idx / 4) * buf.stride + scan[i].idx % 4] = values[i];
  }
}

TCoeff roundQ10(const int64_t value)
{
  // Products of 724 and two TCoeff values cannot approach INT64_MIN/MAX.
  const int64_t rounded = value >= 0 ? (value + 512) / 1024 : -((-value + 512) / 1024);
  CHECK(rounded < std::numeric_limits<TCoeff>::min() || rounded > std::numeric_limits<TCoeff>::max(),
        "GTT4x4 rotation exceeds TCoeff range");
  return TCoeff(rounded);
}

void rotate(std::array<TCoeff, 16> &values, const unsigned first, const unsigned second, const bool inverse)
{
  for (unsigned i = 0; i < 4; ++i)
  {
    const int64_t a = values[first + i];
    const int64_t b = values[second + i];
    values[first + i]  = roundQ10(724LL * (inverse ? a - b : a + b));
    values[second + i] = roundQ10(724LL * (inverse ? a + b : b - a));
  }
}
}

Gtt4x4Path deriveGtt4x4Path(const CCoeffBuf &finalLevels)
{
  const auto values = gather(finalLevels);
  uint8_t parity[2] = { 0, 0 };
  for (unsigned i = 0; i < 8; ++i)
  {
    const int64_t level = values[i];
    const uint64_t magnitude = uint64_t(level < 0 ? -level : level);
    parity[i / 4] ^= uint8_t(magnitude & 1);
  }
  return { parity[0], parity[1] };
}

void forwardGtt4x4Path(const CCoeffBuf &baseRaster, Gtt4x4Path path, CoeffBuf &pathRaster)
{
  CHECK(path.k2 > 1 || path.k3 > 1, "Invalid GTT4x4 kernel path");
  auto values = gather(baseRaster);
  if (path.k2)
  {
    rotate(values, 4, 8, false);
  }
  if (path.k3)
  {
    rotate(values, 8, 12, false);
  }
  scatter(values, pathRaster);
}

void inverseGtt4x4Path(const CCoeffBuf &dequantRaster, Gtt4x4Path path, CoeffBuf &baseRaster)
{
  CHECK(path.k2 > 1 || path.k3 > 1, "Invalid GTT4x4 kernel path");
  auto values = gather(dequantRaster);
  if (path.k3)
  {
    rotate(values, 8, 12, true);
  }
  if (path.k2)
  {
    rotate(values, 4, 8, true);
  }
  scatter(values, baseRaster);
}
#endif
