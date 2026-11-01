#include "EncAppCfg.h"
#include "EncoderLib/EncCfg.h"

#include <exception>
#include <iostream>
#include <memory>

// The real parser and its value object are the only application objects here.
// No EncApp/EncLib, input video, picture, or encode/decode loop is constructed.
int main(int argc, char *argv[])
{
  auto cfg = std::make_unique<EncCfg>();
  EncAppCfg parser;
  try
  {
    const bool accepted = parser.parseCfg(argc, argv, cfg.get());
    std::cout << "PARSE_ONLY_RESULT compile=" << int(HHI_GTT != 0)
              << " default_sub=" << HHI_GTT << " stats_default=" << HHI_GTT_STATS
              << " old_compile=" << HHI_GTT4X4 << " accepted=" << accepted
              << " sub=" << cfg->m_gttExperiment << " stats=" << cfg->m_gttStats
              << " legacy=" << cfg->m_gtt4x4Enabled << '\n';
    return accepted ? 0 : 2;
  }
  catch (const std::exception &error)
  {
    std::cerr << "PARSE_ONLY_EXCEPTION " << error.what() << '\n';
    return 3;
  }
}
