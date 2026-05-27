# MoviePilot V2 Plugin Market

MoviePilot V2 第三方插件仓库。

## 插件列表

| 序号 | 名称 | 当前版本 | 功能简述 | 站点认证 |
|:--:|:--|:--:|:--|:--:|
| 1 | [私密影片整理](javorganizer) | 1.0.0 | 扫描指定目录，识别影片编码，生成 NFO 和图片，并整理到指定媒体库；对无法识别的影片支持保底整理。 | 不需要 |
| 2 | [影视分级补全](mediaratingfiller) | 1.0.0 | 扫描已整理媒体库，补全 NFO 缺失的分级（OMDb/TMDb/地区兜底），支持历史记录与手动修改。 | 不需要 |

## 仓库结构

```text
plugins.v2/
├── README.md
├── javorganizer/
│   ├── __init__.py
│   ├── plugin.py
│   ├── requirements.txt
│   └── ...
└── mediaratingfiller/
    ├── __init__.py
    ├── plugin.py
    ├── scanner.py
    └── ...
```

## 使用方式

将[本仓库](https://github.com/lishi0105/MoviePilot-Plugins)添加到 MoviePilot V2 的第三方插件市场后，在插件市场中安装需要的插件。

如果仓库为私有仓库，请在 MoviePilot 中配置可访问该仓库的 GitHub Token。

## 特别鸣谢

- [MoviePilot](https://github.com/jxxghp/MoviePilot)
- [jxxghp](https://github.com/jxxghp)
