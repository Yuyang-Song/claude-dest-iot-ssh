#pragma once
// ===========================================================================
// Hardware Abstraction Layer
//
// 自从全线切到 m5stack/M5Unified 后,电源 / 按键 / 屏幕的硬件差异
// (StickC Plus 的 AXP192  vs  StickS3 的 M5PM1)大部分由 M5Unified
// 运行时探测板型直接消化。这层 HAL 剩下的价值是:
//   1) 把 M5.Power / M5.Display / M5.BtnPWR 这些新 API 集中在一处,
//      上层 main.cpp 还是按 hw::* 的老名字调,迁移代价小;
//   2) 为未来按板型做特殊适配(如 StickS3 的 M5PM1 L3 省电模式)
//      留一个切入点。
//
// 在 setup() 里 M5.begin() 之后调一次 hw::init()。
// ===========================================================================

#include <stdint.h>

namespace hw {

// 放在 M5.begin() 之后调用一次,完成 LEDC 初始化、按键 pinMode 等。
void init();

// 屏幕背光亮度,0..100。
// StickC Plus: 走 AXP192 LDO2。
// StampS3:    走 LEDC PWM,需在 build_flags 里定义 -DSTAMPS3_BACKLIGHT_PIN=<gpio>。
//              未定义则空操作(适合屏幕常亮或没屏的场景)。
void setScreenBrightness(uint8_t pct_0_100);

// LCD 背光/面板电源。StickC Plus 走 AXP192 LDO2 开关;
// StampS3 没独立 LDO,把亮度设为 0 代替"关"。
void setLcdPower(bool on);

// 软关机。
// StickC Plus: AXP192 切电。
// StampS3:    进 deep sleep(按 Boot / Reset 键唤醒)。
void powerOff();

// 电源遥测。StampS3 裸模组无 PMIC,全部返回 0。
uint16_t getBatVoltageMv();     // 电池电压 mV
int16_t  getBatCurrentMa();     // 充放电流 mA(负值 = 充电)
uint16_t getVbusVoltageMv();    // VBUS/USB 电压 mV;>4000 视为插着 USB
int8_t   getChipTempC();        // 温度 °C(S3 用内部温度传感器)

// PWR 键状态机:0=无动作,1=短按(>=20ms 且 <1s),2=长按(>=1s)。
// StickC Plus: AXP192 内置检测。
// StampS3:    轮询 STAMPS3_PWR_BTN_PIN(默认 GPIO0 / Boot 键,active-low)。
uint8_t  pwrBtnPress();

// 指示 LED(红 LED)控制。
// StickC Plus: 驱动 GPIO10(active-low),和上游协议一致。
// StickS3:    GPIO10 是 Grove SDA,**不能**当 LED;用 M5.Power.setLed
//              驱动板内 PMIC 指示灯(亮度 0/255)。
void setLed(bool on);

} // namespace hw
