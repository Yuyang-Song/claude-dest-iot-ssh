#include "hal_hw.h"
#include <Arduino.h>
#include <M5Unified.h>

// ============================================================================
// 所有电源 / 屏幕亮度 / PWR 键的硬件差异都由 M5Unified 运行时探测板型后路由:
//   StickC Plus → AXP192 / ST7789V2 / 侧面按键
//   StickS3     → M5PM1  / ST7789P3 / 侧面按键
//   (以后加 Plus 2 / CoreS3 / AtomS3 也能直接跑)
// 所以这层 HAL 其实就是转发,历史原因 + 让 main.cpp 不直接依赖具体 API 名。
// ============================================================================

namespace hw {

void init() {
  // M5.begin() 已经初始化了 Display / Btn / Power / Imu / Rtc。
#ifndef TARGET_STICKS3
  // StickC Plus 上红 LED 在 GPIO10,主动驱动;置 HIGH = 关(active-low)。
  pinMode(10, OUTPUT);
  digitalWrite(10, HIGH);
#else
  // StickS3 上 GPIO10 是 Grove SDA,绝对不能 pinMode OUTPUT。
  // 指示 LED 走 M5PM1,这里无需初始化。
  // 如果要 5V 外出(IR TX / Grove 5V),在这里打开:
  //   M5.Power.setExtOutput(true);
#endif
}

void setScreenBrightness(uint8_t pct) {
  if (pct > 100) pct = 100;
  M5.Display.setBrightness((uint8_t)(((uint32_t)pct * 255) / 100));
}

void setLcdPower(bool on) {
  // M5Unified 没有独立 LDO 开关(不同板子实现差异太大),统一用 brightness=0 当关。
  M5.Display.setBrightness(on ? 204 : 0);   // 204 ≈ 80%,等价原 applyBrightness 的默认档
}

void powerOff() {
  M5.Power.powerOff();
}

uint16_t getBatVoltageMv() {
  return (uint16_t)M5.Power.getBatteryVoltage();
}

int16_t getBatCurrentMa() {
  // M5Unified 的 M5.Power 没有公开电流 API,返 0。需要电流只能直接调板型专属库。
  return 0;
}

uint16_t getVbusVoltageMv() {
  // 没有统一的 VBUS 电压 API,用 isCharging() 当二值信号:插着视作 5V。
  return M5.Power.isCharging() ? 5000 : 0;
}

int8_t getChipTempC() {
  // ESP32 / ESP32-S3 都有 temperatureRead(),低核版本找不到就返 0。
  extern float temperatureRead() __attribute__((weak));
  if (temperatureRead) {
    float t = temperatureRead();
    if (t != t) return 0;
    if (t < -40) return -40;
    if (t > 127) return 127;
    return (int8_t)t;
  }
  return 0;
}

uint8_t pwrBtnPress() {
  // M5Unified 把 StickC Plus 的 AXP192 PWR 键、StickS3 的侧面按键都包装成 BtnPWR。
  // 板子没有 PWR 键时 BtnPWR 永远 idle,调用安全。
  if (M5.BtnPWR.pressedFor(1000)) return 2;  // long
  if (M5.BtnPWR.wasReleased())    return 1;  // short
  return 0;
}

void setLed(bool on) {
#ifndef TARGET_STICKS3
  // StickC Plus:GPIO10 直接驱动红 LED,active-low。
  digitalWrite(10, on ? LOW : HIGH);
#else
  // StickS3:M5PM1 上的指示 LED。若库版本没实现 setLed,这里会编不过 —
  // 真遇到就换成 M5.Power.setLed(on ? 255 : 0) 的变体。
  M5.Power.setLed(on ? 255 : 0);
#endif
}

} // namespace hw
