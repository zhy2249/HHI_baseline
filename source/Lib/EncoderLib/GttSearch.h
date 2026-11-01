#ifndef __GTT_SEARCH__
#define __GTT_SEARCH__

#include "CommonLib/TrQuant.h"

#if HHI_GTT_COMMON
#include "CommonLib/Gtt.h"
#include <chrono>
#include <vector>

struct GttSearchSizeStats
{
  uint64_t calls = 0, dqCalls = 0, feasibleCandidates = 0, scoredCandidates = 0;
  uint64_t comparisons = 0, localWins = 0;
  uint64_t dctNs = 0, dqNs = 0, scoreNs = 0;
  long double g1Energy = 0, allEnergy = 0, deltaJSum = 0;
};

struct GttSearchStats
{
  std::array<GttSearchSizeStats, 4> bySize {};
};

GttSearchSizeStats *getGttSearchSizeStats(GttSearchStats *stats, int size);
void recordGttSearchOutcome(GttSearchSizeStats *stats, double bestNonzeroCost, double legacyCost);
void printGttSearchStats(const GttSearchStats &stats, int layer, int sub);

// Observation only: no clock reads or writes when statistics are disabled.
class GttSearchTimer
{
public:
  explicit GttSearchTimer(uint64_t *elapsed) : m_elapsed(elapsed)
  {
    if (m_elapsed) { m_start = std::chrono::steady_clock::now(); }
  }
  ~GttSearchTimer()
  {
    if (m_elapsed)
    {
      *m_elapsed += uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now() - m_start).count());
    }
  }
private:
  uint64_t *m_elapsed;
  std::chrono::steady_clock::time_point m_start;
};

// The caller owns the prediction, QP/lambda setup, and complete RD scoring.
// This generator leaves TU levels untouched; its input TU must be in GTT mode.
std::vector<Gtt4x4QuantResult> generateGtt4x4Candidates(
  TrQuant &trQuant, const TransformUnit &tu, const CPelBuf &residual, const Ctx &startCtx);

// A zero result is normalized to the legacy CBF=0 representation.
void installGtt4x4Candidate(TransformUnit &tu, const Gtt4x4QuantResult &result);

// Version-aware entry used by both prediction searches. Version 2 retains the
// frozen candidate generator; version 3 uses the SPS layout and multi-CG DQ.
std::vector<GttQuantResult> generateGttCandidates(
  TrQuant &trQuant, const TransformUnit &tu, const CPelBuf &residual, const Ctx &startCtx,
  GttSearchStats *stats = nullptr);
void installGttCandidate(TransformUnit &tu, const GttQuantResult &result);
#endif

#endif
