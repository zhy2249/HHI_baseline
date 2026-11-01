#ifndef HHI_GTT_DCT_H
#define HHI_GTT_DCT_H

#include "CommonDef.h"

#if HHI_GTT_COMMON
#include "Unit.h"
#include "Buffer.h"
constexpr const char *GTT_DCT_IMPLEMENTATION = "int64-partial-butterfly";

// Wide scalar partial butterflies use the baseline integer matrices. No stage
// packs to int16; only the baseline inverse-stage clips are applied.
void forwardGttDctWide(const CPelBuf &resi, CoeffBuf &coeff, int bitDepth, int dynamicRange);
void inverseGttDctWide(const CCoeffBuf &coeff, PelBuf &resi, int bitDepth, int dynamicRange);
#endif
#endif
