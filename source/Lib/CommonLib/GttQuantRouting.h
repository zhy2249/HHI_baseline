#ifndef GTT_QUANT_ROUTING_H
#define GTT_QUANT_ROUTING_H

#include "Gtt.h"

#if HHI_GTT_COMMON
namespace GttQuantRouting
{
inline int groupAt(const GttLayout &layout, const int scanIdx)
{
  CHECK(scanIdx < 0 || scanIdx >= layout.numCoeff, "GTT scan position outside layout");
  for (int group = 0; group < 4; ++group)
  {
    if (scanIdx >= layout.groupBegin[group] && scanIdx < layout.groupEnd[group])
    {
      return group;
    }
  }
  THROW("GTT layout has a gap");
}

inline int finishGroup(const int physicalState, int parity, const int scanIdx, const GttLayout &layout,
                       const GttConstraints &constraints)
{
  const int group = groupAt(layout, scanIdx);
  const int required = constraints.requiredParity[group];
  if (required < 0)
  {
    parity = 0;
  }
  if (scanIdx == layout.groupBegin[group])
  {
    if (required >= 0 && parity != required)
    {
      return -1;
    }
    parity = 0;
  }
  return 2 * physicalState + parity;
}

inline int nextSlot(const int previousSlot, const int levelParity, const int scanIdx, const GttLayout &layout,
                    const GttConstraints &constraints, const uint64_t physicalTransitions)
{
  CHECK(previousSlot < 0 || previousSlot >= 8 || levelParity < 0 || levelParity > 1,
        "Invalid GTT joint transition");
  const int physicalState = previousSlot / 2;
  const int nextState = int((physicalTransitions >> ((physicalState << 3) + (levelParity << 2))) & 15);
  CHECK(nextState >= 4, "GTT requires the four-state DQ1 transition table");
  return finishGroup(nextState, (previousSlot & 1) ^ levelParity, scanIdx, layout, constraints);
}

inline int skipSlot(const int snapshotSlot, const int scanIdx, const GttLayout &layout,
                    const GttConstraints &constraints)
{
  CHECK(snapshotSlot < 0 || snapshotSlot >= 8 || scanIdx % layout.cgSize != 0 || layout.cgSize != 16,
        "Invalid GTT coding-group skip");
  // Sixteen DQ1 zero transitions are the identity. A skipped CG cannot
  // cross a logical boundary in the supported multi-CG layouts.
  CHECK(groupAt(layout, scanIdx) != groupAt(layout, scanIdx + layout.cgSize - 1),
        "GTT coding-group skip crosses a logical group");
  return finishGroup(snapshotSlot / 2, snapshotSlot & 1, scanIdx, layout, constraints);
}

inline bool lastStartAllowed(const int scanIdx, const GttLayout &layout, const GttConstraints &constraints)
{
  for (int group = groupAt(layout, scanIdx) + 1; group < 4; ++group)
  {
    if (constraints.requiredParity[group] == 1)
    {
      return false;
    }
  }
  return true;
}
}
#endif
#endif
