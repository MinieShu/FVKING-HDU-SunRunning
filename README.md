# iOS FVKING HDU SUNRUNNING

一个用于 iOS 设备虚拟定位注入工具，支持单点坐标注入、清除虚拟定位，以及按地图路线模拟跑步轨迹。用于hdu的傻子阳光长跑，下雨天还要跑是哪个领导想出来的。

项目包含两个界面：

- `ios_location_injector.py`：Tkinter 桌面界面。
- `ios_location_injector_web.py`：FastAPI Web 界面。

## 适用场景

- iOS 开发、测试和个人学习时临时模拟定位。
- 在地图上规划跑步路线，并按指定速度、间隔和圈数逐点注入定位。
- 需要快速恢复真实定位时，一键清除虚拟定位。

## 功能

- 连接已配对的 iOS 设备。
- 输入经纬度并注入虚拟定位。
- 在地图上点击生成路线点。
- 按速度、间隔、随机漂移和圈数模拟绕圈跑步轨迹。
- 点击已有标定点可选为闭合点，选中点会放大显示；每次回到该点记录为完成一圈。
- 清除设备上的虚拟定位。

## 环境要求

- macOS 12 或更新版本。
- Python 3.10 或更新版本。
- Xcode 或 Apple iOS 开发组件。
- iPhone/iPad 已开启开发者模式。
- iPhone/iPad 已连接并信任当前 Mac。
- `pymobiledevice3` 可以访问设备，并且 DeveloperDiskImage 可用。
- 首次配对建议使用 USB 数据线连接；配对成功后，如系统和网络环境支持，可尝试同一局域网下无线连接。

### iPhone 准备

1. 使用 USB 数据线连接 iPhone 和 Mac。
2. iPhone 弹出提示时选择“信任此电脑”，并输入锁屏密码。
3. 在 iPhone 上打开“设置” -> “隐私与安全性” -> “开发者模式”，开启后按提示重启。
4. 保持手机解锁，避免连接或注入过程中设备休眠。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

检查设备是否能被识别：

```bash
python3 -m pymobiledevice3 lockdown info
```

如果提示 DeveloperDiskImage 未挂载，可以先执行：

```bash
python3 -m pymobiledevice3 mounter auto-mount
```

如果使用 iOS 17 或更新版本，并且提示需要 tunnel，可以另开一个终端执行：

```bash
sudo python3 -m pymobiledevice3 remote tunneld
```

## 运行

桌面版：

```bash
python ios_location_injector.py
```

Web 版：

```bash
python ios_location_injector_web.py
```

启动后终端会输出本地访问地址，例如：

```text
浏览器界面已启动：http://127.0.0.1:12345
```

## 使用说明

### 单点虚拟定位

1. 连接 iPhone，并确认手机已经信任当前 Mac。
2. 启动桌面版或 Web 版。
3. 点击“连接/刷新设备”，等待界面显示设备信息。
4. 在地图上点击目标位置，或手动输入 GCJ-02 经纬度。
5. 点击“注入当前坐标”。
6. 打开手机上的地图类应用，确认定位已经变化。

### 路线跑步模拟

1. 在地图上依次点击路线点，至少添加两个点。
2. 如果需要绕圈，点击已有标定点作为闭合点；选中点会放大显示。
3. 设置速度、注入间隔、步态摆动和模拟圈数。
4. 点击“开始轨迹跑步”。
5. 跑步过程中界面会显示当前进度、完成圈数和累计距离。
6. 需要提前停止时，点击“停止轨迹跑步”。

### 清除虚拟定位

使用完毕后点击“清除虚拟定位”。如果仍未恢复真实定位，可以拔掉数据线、重启定位应用，或重启 iPhone。

## 常见问题

### 必须一直用数据线连接吗？

首次配对和信任设备通常需要 USB 数据线。配对完成后，如果 Xcode/iOS 设备管理支持无线调试，并且 Mac 与 iPhone 在同一局域网内，可以尝试无线连接；连接不稳定时建议继续使用 USB。

### 连接设备失败怎么办？

- 确认 iPhone 已解锁并信任当前 Mac。
- 确认开发者模式已经开启。
- 重新插拔 USB 数据线，或更换数据线和 USB 接口。
- 执行 `python3 -m pymobiledevice3 lockdown info` 检查设备是否可访问。
- 执行 `python3 -m pymobiledevice3 mounter auto-mount` 挂载 DeveloperDiskImage。

### Web 版打不开怎么办？

- 查看终端输出的本地地址，例如 `http://127.0.0.1:12345`。
- 确认虚拟环境已经激活，并已执行 `pip install -r requirements.txt`。
- 如果端口被占用，重新运行脚本，程序会自动选择可用端口。

## 注意事项

- 地图坐标使用 GCJ-02，注入设备前会自动转换为 WGS-84。
- 本工具仅用于开发、测试和个人学习场景。
- iOS 版本、Xcode 版本和 `pymobiledevice3` 版本可能影响连接能力。
- 由于阳光乐跑会采集手机加速度数据，这方面需要通过合适的摇晃手机以模仿步频，所有切记启动程序后摇晃手机
- 模拟定位期间不要频繁切换网络、锁屏或断开设备连接。
