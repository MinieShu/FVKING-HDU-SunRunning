# iOS Location Injector

一个用于 iOS 设备虚拟定位注入的 Python 工具，支持单点坐标注入、清除虚拟定位，以及按地图路线模拟跑步轨迹。

项目包含两个界面：

- `ios_location_injector.py`：Tkinter 桌面界面。
- `ios_location_injector_web.py`：FastAPI Web 界面。

## 功能

- 连接已配对的 iOS 设备。
- 输入经纬度并注入虚拟定位。
- 在地图上点击生成路线点。
- 按速度、间隔和随机漂移模拟跑步轨迹。
- 清除设备上的虚拟定位。

## 环境要求

- macOS
- Python 3.10+
- 已安装 Xcode 或相关 iOS 开发组件
- 已通过 USB 连接并信任 iPhone
- DeveloperDiskImage 可用

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

如果提示 DeveloperDiskImage 未挂载，可以先执行：

```bash
python3 -m pymobiledevice3 mounter auto-mount
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

1. 通过 USB 连接 iPhone，并在手机上信任此电脑。
2. 点击“连接/刷新设备”。
3. 输入或在地图上选择 GCJ-02 坐标。
4. 点击“注入当前坐标”。
5. 如需模拟路线，在地图上至少添加两个路线点，设置速度、间隔和随机浮动后点击“开始轨迹跑步”。
6. 使用完毕后点击“清除虚拟定位”。

## 注意事项

- 地图坐标使用 GCJ-02，注入设备前会自动转换为 WGS-84。
- 本工具仅用于开发、测试和个人学习场景。
- iOS 版本、Xcode 版本和 `pymobiledevice3` 版本可能影响连接能力。

