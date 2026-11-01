#ifndef GTT4X4_QUANT_ROUTING_H
#define GTT4X4_QUANT_ROUTING_H

#include "CommonDef.h"

#if HHI_GTT_COMMON
#include <array>

namespace Gtt4x4QuantRouting
{
// A joint slot retains the original physical DQ state in its high bits. Only
// the added parity is reset at a logical four-coefficient boundary.
inline int nextSlot(int previousSlot, int levelParity, int scanIdx, int requiredParity,
                    uint64_t physicalTransitions)
{
  const int physicalState = previousSlot / 2;
  const int nextState = int((physicalTransitions >> ((physicalState << 3) + (levelParity << 2))) & 15);
  CHECK(nextState >= 4, "Unexpected GTT DQ state transition");
  int nextParity = requiredParity < 0 ? 0 : ((previousSlot & 1) ^ levelParity);
  if ((scanIdx & 3) == 0)
  {
    if (requiredParity >= 0 && nextParity != requiredParity)
    {
      return -1;
    }
    nextParity = 0;
  }
  return 2 * nextState + nextParity;
}

inline bool lastStartAllowed(int scanIdx, const std::array<int8_t, 4> &requiredParity)
{
  for (int group = scanIdx / 4 + 1; group < 4; group++)
  {
    if (requiredParity[group] == 1)
    {
      return false;
    }
  }
  return true;
}
}
#endif

#endif
