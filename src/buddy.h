#pragma once
#include <stdint.h>
#include <M5Unified.h>   // 提供 M5GFX / M5Canvas / lgfx::LGFXBase

// Multi-species ASCII buddy renderer. Each species lives in its own
// src/buddies/<name>.cpp file and exposes 7 state functions matching
// the PersonaState enum order: sleep, idle, busy, attention, celebrate,
// dizzy, heart.
void buddyInit();
void buddyTick(uint8_t personaState);
void buddyInvalidate();
// 渲染目标用 lgfx::LGFXBase(M5GFX 和 M5Canvas 共同基类),这样既能传 &M5.Lcd 也能传 &spr。
void buddyRenderTo(lgfx::LGFXBase* tgt, uint8_t personaState);
void buddySetSpecies(const char* name);
void buddySetSpeciesIdx(uint8_t idx);
void buddyNextSpecies();
void buddySetPeek(bool peek);
uint8_t buddySpeciesIdx();
uint8_t buddySpeciesCount();
const char* buddySpeciesName();

// Per-species state function: takes the global tickCount and renders
// the buddy + any overlays for the current state into the shared sprite.
typedef void (*StateFn)(uint32_t t);

struct Species {
  const char* name;
  uint16_t bodyColor;
  StateFn states[7];   // index by PersonaState (0=sleep .. 6=heart)
};
