#ifndef __GTT_SEARCH__
#define __GTT_SEARCH__

#include "CommonLib/TrQuant.h"

#if HHI_GTT4X4
#include <vector>

// The caller owns the prediction, QP/lambda setup, and complete RD scoring.
// This generator leaves TU levels untouched; its input TU must be in GTT mode.
std::vector<Gtt4x4QuantResult> generateGtt4x4Candidates(
  TrQuant &trQuant, const TransformUnit &tu, const CPelBuf &residual, const Ctx &startCtx);

// A zero result is normalized to the legacy CBF=0 representation.
void installGtt4x4Candidate(TransformUnit &tu, const Gtt4x4QuantResult &result);
#endif

#endif
