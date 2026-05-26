# MoviePilot V2 - 影片自动归档与元数据整理插件设计方案

## 1. 项目目标

开发一个基于 MoviePilot V2 的插件，实现：

- 自动扫描指定目录中的视频文件
- 判断文件是否下载完成（文件稳定）
- 自动识别影片编码
- 自动获取影片元数据
- 自动生成 `movie.nfo`、`poster.jpg`、`fanart.jpg`
- 自动迁移整理视频文件
- 对无法识别的影片进行“保底整理”
- 支持定时扫描与手动触发
- 支持失败记录与重复处理保护

插件定位：

```text
MoviePilot：
负责下载与插件运行

本插件：
负责影片归档、元数据整理、迁移、NFO生成

Emby/Jellyfin：
负责最终展示与媒体库管理
```

---

# 2. 功能设计

## 2.1 目录映射配置

支持多个目录映射。

配置格式：

```text
{src_dir}:{dst_dir}
```

示例：

```text
/downloads/videos:/media/Videos
/downloads/private:/media/Private
```

说明：

| 配置项 | 说明 |
|---|---|
| src_dir | 扫描目录 |
| dst_dir | 影片整理目标目录 |
| 未配置 dst_dir | 默认在当前目录创建新目录 |

---

## 2.2 扫描模式

支持：

### 手动触发

用户点击插件页面按钮：

```text
立即扫描
```

立即执行一次扫描。

### 自动扫描

插件定时扫描目录。

配置：

```text
扫描周期（分钟）
```

默认：

```text
10 分钟
```

---

# 3. 文件稳定判断

## 3.1 设计目的

防止：

- BT 下载未完成
- qBittorrent 边下边写
- 文件仍在增长
- 视频尚未生成完成

导致提前迁移。

## 3.2 判断逻辑

记录：

```text
文件路径
文件大小
mtime
最后检测时间
稳定命中次数
```

满足以下条件认为文件稳定：

```text
文件进入待检测队列后，先等待 stable_wait_seconds
之后按 stable_check_interval_seconds 周期检测
连续 stable_check_count 次 size 和 mtime 都不变
```

默认：

```text
stable_wait_seconds = 30
stable_check_interval_seconds = 30
stable_check_count = 3
```

---

# 4. 影片编码识别逻辑

## 4.1 支持格式

示例：

```text
SSIS-123
IPX-999
PRED-456
FC2-PPV-1234567
HEYZO-1234
```

## 4.2 文件名处理

自动：

- 转大写
- 去除分辨率标签
- 去除字幕标签
- 去除版本标签
- 去除资源站标签

例如：

```text
[ABC] SSIS-123-C 1080p.mp4
↓
SSIS-123
```

## 4.3 影片判断条件

满足：

```text
匹配影片编码规则
```

则认为是可归档影片文件。

否则：

```text
进入保底整理流程
```

---

# 5. 影片元数据整理

## 5.1 刮削流程

```text
本地缓存
    ↓
站点元数据解析
    ↓
生成本地元数据
```

## 5.2 支持数据源（一期）

建议：

| 数据源 | 方式 |
|---|---|
| site_a | 网页解析 |
| site_b | 网页解析 |
| api_provider | 二期 API |

## 5.3 一期不依赖 API Token

一期：

```text
直接网页解析
```

不要求：

- API Token
- 用户注册

## 5.4 二期扩展

支持：

```text
扩展元数据 API
```

配置：

```text
api_id
api_secret
```

---

# 6. 影片整理流程

## 6.1 创建目标目录

格式：

```text
{dst_dir}/{影片编码}/
```

示例：

```text
/media/Videos/SSIS-123/
```

## 6.2 生成文件

目录内容：

```text
SSIS-123/
    SSIS-123.mp4
    movie.nfo
    poster.jpg
    fanart.jpg
```

## 6.3 生成内容

### movie.nfo

包含：

- 标题
- 影片编码
- 演员
- 简介
- 标签
- 发布时间
- 时长
- 封面路径

### poster.jpg

竖版海报。

优先：

```text
官方封面
```

失败：

```text
视频截图
```

### fanart.jpg

横版背景图。

优先：

```text
官方背景图
```

失败：

```text
视频截图
```

## 6.4 视频迁移

建议：

```text
先生成元数据
后移动视频
```

避免：

```text
元数据失败但视频已移动
```

---

# 7. 保底整理功能

## 7.1 功能目标

对于：

- 非目标影片
- 编码识别失败
- 元数据失败

的文件进行基础整理。

避免：

```text
文件散落
无封面
无信息
```

## 7.2 保底目录配置

配置：

```text
fallback_dir
```

示例：

```text
/media/未识别影片
```

## 7.3 整理结构

格式：

```text
fallback_dir/原文件名/
```

示例：

```text
/media/未识别影片/test_video/
```

## 7.4 自动生成内容

### poster.jpg

使用 ffmpeg 截图。

### fanart.jpg

使用 ffmpeg 截图。

### movie.nfo

自动生成基础信息：

| 字段 | 来源 |
|---|---|
| 文件名 | 文件名 |
| 文件大小 | ffprobe |
| 分辨率 | ffprobe |
| 时长 | ffprobe |
| 编码格式 | ffprobe |
| 创建时间 | 文件属性 |
| 原始路径 | 扫描路径 |

---

# 8. FFmpeg 处理

## 8.1 依赖

需要：

```text
ffmpeg
ffprobe
```

## 8.2 截图策略

默认：

```text
视频 10% 位置
```

支持配置：

```text
00:05:00
10%
20%
```

## 8.3 示例命令

```bash
ffmpeg -ss 00:05:00 -i input.mp4 -frames:v 1 poster.jpg
```

---

# 9. 数据持久化

## 9.1 记录内容

需要记录：

| 字段 | 说明 |
|---|---|
| path | 文件路径 |
| size | 文件大小 |
| mtime | 修改时间 |
| status | 当前状态 |
| jav_code | 影片编码 |
| last_scan | 最后扫描 |
| error | 错误原因 |

## 9.2 状态设计

建议：

| 状态 | 说明 |
|---|---|
| pending | 待稳定 |
| processing | 正在处理 |
| jav_success | 影片整理成功 |
| fallback | 保底整理成功 |
| failed | 整理失败 |
| ignored | 已忽略 |

---

# 10. 重复处理保护

## 10.1 防止重复迁移

同一文件：

```text
path + size + mtime
```

一致则不重复处理。

## 10.2 防止循环扫描

处理完成后：

```text
加入 processed 记录
```

后续跳过。

---

# 11. 异常处理

## 11.1 刮削失败

可配置：

| 策略 | 说明 |
|---|---|
| fallback | 进入保底整理 |
| skip | 跳过 |
| retry | 下次重试 |

## 11.2 目标目录已存在

支持：

| 策略 | 说明 |
|---|---|
| overwrite | 覆盖 |
| rename | 自动重命名 |
| skip | 跳过 |

---

# 12. 插件配置项

| 配置项 | 说明 |
|---|---|
| enabled | 插件开关 |
| scan_interval | 扫描周期 |
| monitor_dirs | 目录映射 |
| fallback_dir | 保底目录 |
| stable_wait_seconds | 稳定等待（秒） |
| stable_check_interval_seconds | 稳定检测间隔（秒） |
| stable_check_count | 稳定检测次数 |
| screenshot_position | 截图时间点 |
| scraper_sources | 刮削源 |
| proxy | 代理 |
| retry_count | 重试次数 |

---

# 13. 插件目录结构建议

```text
package.v2.json
plugins.v2/
    javorganizer/
        __init__.py
        plugin.py
        scraper.py
        parser.py
        nfo.py
        ffmpeg.py
        scanner.py
        storage.py
        sync2movie.py
        requirements.txt
```

---

# 14. 插件部署方式

## 14.1 当前文件说明

当前实现目录：

```text
plugs/plugins.v2/javorganizer/
```

插件索引文件：

```text
plugs/package.v2.json
```

`package.v2.json` 中的插件 ID 为：

```text
JavOrganizer
```

插件源码目录名是 `javorganizer`，插件类名是 `JavOrganizer`。


## 14.2 作为 V2 插件仓库部署

如果通过 MoviePilot V2 第三方插件仓库安装，需要保留插件索引文件：

```text
package.v2.json
```

推荐仓库结构：

```text
package.v2.json
plugins.v2/
    javorganizer/
        __init__.py
        plugin.py
        scraper.py
        parser.py
        nfo.py
        ffmpeg.py
        scanner.py
        storage.py
        jav_logger.py
        requirements.txt
```

部署步骤：

```text
1. 将插件仓库地址添加到 MoviePilot 的第三方插件市场
2. 在插件市场刷新插件列表
3. 找到“私密影片自动归档”
4. 安装并启用插件
5. 配置扫描目录、目标目录、保底目录和扫描周期
```

## 14.3 依赖与权限

运行环境需要可用的命令：

```text
ffmpeg
ffprobe
```

需要确认 MoviePilot 对以下目录有读写权限：

| 目录 | 权限 |
|---|---|
| 扫描目录 | 读取、移动或复制 |
| 目标媒体库目录 | 创建目录、写入文件 |
| 保底目录 | 创建目录、写入文件 |
| `/config/plugins/javorganizer/` | 写入 sqlite 状态库 |

如果使用移动模式，源目录和目标目录最好在同一套 Docker 挂载路径下，避免跨设备移动失败。

## 14.4 启用后的基础配置

至少配置：

```text
enabled=true
monitor_dirs=/downloads/videos:/media/Videos
fallback_dir=/media/未识别影片
stable_minutes=5
scan_interval=10
```

配置后可以先执行一次：

```text
立即扫描
```

确认日志中出现扫描、识别、截图、NFO 生成和迁移记录。

---

# 15. 一期开发范围（推荐）

建议一期只实现：

- 定时扫描
- 文件稳定判断
- 影片编码识别
- 元数据刮削
- NFO生成
- FFmpeg截图
- 视频迁移
- 保底整理
- 失败记录

暂不实现：

- 演员头像
- 演员库
- 合集
- 多演员关系
- Web UI复杂管理
- 在线预览

---

# 16. 后续可扩展方向

## 二期

- 扩展元数据 API
- 演员头像
- 多数据源融合
- 中文字幕识别
- 版本识别
- 4K识别
- 系列识别
- 评分聚合

## 三期

- Web 管理界面
- 手动修正影片编码
- 批量重刮
- 智能命名模板
- Emby/Jellyfin API联动
- 自动刷新媒体库
- AI封面筛选
- AI编码识别

---

# 17. 推荐技术方案

| 功能 | 技术 |
|---|---|
| 网页解析 | requests + bs4 |
| 视频信息 | ffprobe |
| 截图 | ffmpeg |
| 数据存储 | sqlite |
| 定时任务 | APScheduler |
| 文件监听（二期） | watchdog |

---

# 18. 推荐开发顺序

建议：

```text
第一步：
扫描 + 稳定判断

第二步：
编码识别

第三步：
迁移整理

第四步：
ffmpeg截图

第五步：
NFO生成

第六步：
元数据整理

第七步：
失败重试
```

不要一开始就做：

```text
复杂元数据
复杂UI
多数据源聚合
```

先把“稳定自动整理”做扎实。
