# MoviePilot V2 插件需求：影视分级补全

## 1. 插件目标

开发一个 MoviePilot V2 插件，用于扫描已整理好的媒体库，补全影视剧 NFO 中缺失的分级信息。

插件只处理最终媒体库，不处理下载目录、刷流目录、临时目录。

推荐插件命名：

```text
中文名：影视分级补全
英文名：Media Rating Filler
插件 ID：media_rating_filler
目录名：mediaratingfiller
```

核心流程：

```text
扫描全部媒体库
→ 按规则识别主 NFO
→ 输出总 NFO 数
→ 解析所有 NFO 元数据
→ 已有分级记录并跳过
→ 无分级且无 imdbid/tmdbid 记录错误
→ 无分级且有 imdbid/tmdbid 加入待处理队列
→ OMDb / TMDb 查询
→ 查不到则地区兜底
→ 写回 NFO
→ SQLite 记录
→ 页面展示、筛选、手动修改
```

---

## 2. 配置项

| 配置项            | 说明                                 |
| ----------------- | ------------------------------------ |
| 启用插件          | 插件总开关                           |
| 媒体库路径        | 支持多个路径，使用换行或英文逗号分隔 |
| 排除目录          | 支持多个目录，使用换行或英文逗号分隔 |
| OMDb API Key      | 用于通过 IMDb ID 查询分级            |
| TMDb API Key      | 用于通过 TMDb ID 查询分级            |
| 单次 API 调用限额 | 控制单次扫描最多调用多少次外部 API，默认5   |
| 每日 API 调用限额 | 控制每天最多调用多少次外部 API，默认800       |
| 请求间隔          | 每次外部 API 请求之间的间隔，默认0.2s          |
| 大陆地区兜底分级  | 默认 PG-13                           |
| 其他地区兜底分级  | 默认 R                               |
| 清空历史记录      | 清空插件 SQLite 处理记录             |

---

## 3. 清空历史记录

设置页增加“清空历史记录”功能。

清空范围：

```text
清空 SQLite 中的处理历史记录
不删除 OMDb/TMDb 查询缓存
不修改任何 NFO 文件
不还原已经写入的分级
不影响插件配置
```

清空前弹出确认提示：

```text
确定要清空历史记录吗？该操作不会修改 NFO，但会删除插件页面中的处理记录。
```

---

## 4. 媒体库扫描

媒体库路径支持多个：

```text
/volume1/media/电影
/volume1/media/剧集
/volume1/media/动漫
```

也支持英文逗号分隔：

```text
/volume1/media/电影,/volume1/media/剧集
```

默认排除目录：

```text
@eaDir
#recycle
.recycle
downloads
manual
brush
tmp
temp
incomplete
```

---

## 5. NFO 识别规则

### 5.1 电影 NFO

电影目录下不固定要求 `movie.nfo`，需要兼容电影名 NFO。

识别顺序：

```text
1. movie.nfo
2. 与视频文件同名的 .nfo
3. 目录下唯一的非 tvshow.nfo 文件
```

示例：

```text
流浪地球2 (2023)/
  流浪地球2 (2023).mkv
  流浪地球2 (2023).nfo
```

这种情况下应识别：

```text
流浪地球2 (2023).nfo
```

如果同一目录存在多个 NFO 文件，按照上述规则只选择一个主 NFO 文件，避免重复处理。

### 5.2 剧集 NFO

剧集只处理：

```text
tvshow.nfo
```

一期不处理分集 NFO：

```text
S01E01.nfo
S01E02.nfo
第01集.nfo
```

---

## 6. NFO 读取字段

从 NFO 中读取以下字段：

| 字段                                 | 用途             |
| ------------------------------------ | ---------------- |
| title                                | 展示名称         |
| year                                 | 年份             |
| imdbid                               | OMDb 查询主键    |
| tmdbid                               | TMDb 查询主键    |
| country                              | 判断地区兜底     |
| mpaa / certification / contentrating | 判断是否已有分级 |

IMDb ID 兼容格式：

```xml
<uniqueid type="imdb">tt1234567</uniqueid>
<imdbid>tt1234567</imdbid>
```

TMDb ID 兼容格式：

```xml
<uniqueid type="tmdb">123456</uniqueid>
<tmdbid>123456</tmdbid>
```

已有分级字段兼容：

```xml
<mpaa>PG-13</mpaa>
<certification>PG-13</certification>
<contentrating>PG-13</contentrating>
```

---

## 7. 分级补全优先级

```text
1. NFO 已有有效分级：记录并跳过
2. NFO 无分级，且有 imdbid：通过 OMDb 查询 Rated
3. OMDb 查不到，且有 tmdbid：通过 TMDb 查询分级
4. OMDb/TMDb 都查不到：按地区兜底
```

最终优先级：

```text
已有分级 > OMDb Rated > TMDb 分级 > 地区兜底
```

---

## 8. OMDb 查询

OMDb 查询使用 IMDb ID。

请求格式：

```text
https://www.omdbapi.com/?i={imdbid}&apikey={api_key}
```

读取字段：

```text
Rated
```

无效结果：

```text
空值
N/A
Unknown
Not Rated
Unrated
接口异常
```

OMDb 无有效结果时，继续尝试 TMDb。

---

## 9. TMDb 查询

TMDb 作为 OMDb 失败后的二级数据源。

### 9.1 电影分级查询

电影使用：

```text
https://api.themoviedb.org/3/movie/{tmdbid}/release_dates?api_key={api_key}
```

处理规则：

```text
读取 results
优先查找 iso_3166_1 = US
从 release_dates 中读取 certification
取第一个非空 certification
```

### 9.2 剧集分级查询

剧集使用：

```text
https://api.themoviedb.org/3/tv/{tmdbid}/content_ratings?api_key={api_key}
```

处理规则：

```text
读取 results
优先查找 iso_3166_1 = US
读取 rating
```

### 9.3 不做分级转换

一期不做国家分级转换。

原因：

```text
大陆地区没有统一影视分级体系
不同国家分级标准不一致
插件目标是补全展示字段，不做复杂分级体系换算
```

处理原则：

```text
优先使用 US 分级
没有 US 分级时，可取第一个非空原始分级
不转换、不映射
仍无有效分级则进入地区兜底
```

---

## 10. 地区兜底规则

当 OMDb 和 TMDb 都查不到有效分级时，按地区兜底。

大陆地区判断：

```text
country 包含 中国 / 中国大陆 / China
路径包含 大陆 / 国产
```

兜底规则：

```text
大陆地区：PG-13
其他地区：R
```

说明：

```text
大陆没有统一分级体系，PG-13 仅作为媒体库管理和家长控制的默认展示值。
```

---

## 11. NFO 写回规则

写回字段：

```xml
<mpaa>PG-13</mpaa>
<certification>PG-13</certification>
```

写回原则：

```text
默认只补充缺失分级
不覆盖已有有效分级
手动修改分级时允许覆盖
写入前备份原 NFO
```

备份文件：

```text
原文件名.nfo.bak_rating
```

---

## 12. SQLite 记录

插件内置 SQLite，用于保存扫描和处理记录。

### 12.1 历史记录表

```sql
CREATE TABLE IF NOT EXISTS rating_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_path TEXT,
    nfo_path TEXT NOT NULL,
    media_type TEXT,
    title TEXT,
    year TEXT,
    imdbid TEXT,
    tmdbid TEXT,
    country TEXT,
    old_rating TEXT,
    new_rating TEXT,
    rating_source TEXT,
    status TEXT,
    error TEXT,
    nfo_mtime REAL,
    nfo_size INTEGER,
    created_at TEXT,
    updated_at TEXT,
    last_scan_at TEXT
);
```

### 12.2 API 缓存表

```sql
CREATE TABLE IF NOT EXISTS rating_api_cache (
    cache_key TEXT PRIMARY KEY,
    source TEXT,
    media_type TEXT,
    imdbid TEXT,
    tmdbid TEXT,
    rating TEXT,
    response_json TEXT,
    success INTEGER,
    error TEXT,
    fetched_at TEXT
);
```

`cache_key` 示例：

```text
omdb:tt1234567
tmdb:movie:123456
tmdb:tv:654321
```

### 12.3 API 用量表

```sql
CREATE TABLE IF NOT EXISTS api_usage (
    day TEXT PRIMARY KEY,
    used_count INTEGER DEFAULT 0,
    updated_at TEXT
);
```

---

## 13. 历史记录展示

插件页面展示处理历史。

字段：

| 字段     | 说明                                       |
| -------- | ------------------------------------------ |
| 标题     | title                                      |
| 类型     | movie / tvshow                             |
| 年份     | year                                       |
| IMDb ID  | imdbid                                     |
| TMDb ID  | tmdbid                                     |
| 国家地区 | country                                    |
| 原分级   | old_rating                                 |
| 新分级   | new_rating                                 |
| 来源     | existing / omdb / tmdb / fallback / manual |
| 状态     | 处理状态                                   |
| 错误信息 | error                                      |
| 更新时间 | updated_at                                 |
| 操作     | 手动修改分级                               |

---

## 14. 历史记录筛选

历史记录页面支持单独筛选或组合筛选。

筛选字段：

```text
国家地区
新分级
处理状态
年份
类型
```

筛选方式：

```text
可以只按一个字段筛选
也可以多个字段组合筛选
筛选结果实时刷新或点击查询后刷新
支持清空筛选条件
```

示例：

```text
国家地区 = 美国
新分级 = R
处理状态 = updated_tmdb
年份 = 2024
类型 = movie
```

页面建议保留统计信息：

```text
当前筛选结果数量
总记录数量
成功数量
失败数量
兜底数量
手动修改数量
```

---

## 15. 手动修改分级

每条历史记录支持手动修改分级。

操作入口：

```text
历史记录列表 → 修改分级
```

可输入示例：

```text
G
PG
PG-13
R
NC-17
TV-PG
TV-14
TV-MA
未分级
```

不强制限制枚举，允许用户手动输入。

处理流程：

```text
读取该记录对应的 nfo_path
校验 NFO 文件是否存在
备份原 NFO
将新分级写入 mpaa 和 certification
更新 SQLite 记录
```

更新字段：

```text
old_rating = 修改前分级
new_rating = 用户输入的新分级
rating_source = manual
status = manual_updated
updated_at = 当前时间
```

失败状态：

```text
manual_failed
```

---

## 16. 日志系统

插件需要接入 MoviePilot 自身日志系统，不单独实现独立日志文件。

日志要求：

```text
使用 MoviePilot 插件日志输出方式
扫描开始和结束必须输出日志
NFO 识别开始和结束必须输出日志
分级处理开始和结束必须输出日志
结束时必须输出汇总记录
异常要输出错误日志
```

---

## 17. 扫描与 NFO 识别日志

### 17.1 扫描阶段

扫描阶段包括：

```text
扫描所有媒体库路径
按照 5.1 / 5.2 规则识别主 NFO 文件
生成全部待识别 NFO 文件列表
输出总 NFO 文件数
```

扫描开始日志：

```text
【影视分级补全】开始扫描媒体库，媒体库路径数量：N
```

扫描进度日志：

```text
【影视分级补全】扫描中，已扫描目录：X，已发现 NFO：Y
```

扫描进度输出频率：

```text
每 5 秒输出一次
```

扫描结束日志：

```text
【影视分级补全】媒体库扫描完成，总 NFO 文件数：N，耗时：X 秒
```

### 17.2 NFO 识别阶段

NFO 识别阶段包括：

```text
读取所有已发现的 NFO
识别 title / imdbid / tmdbid / country / rating/mpaa
判断是否已有有效分级
判断是否缺少 imdbid 和 tmdbid
生成待处理队列
```

识别开始日志：

```text
【影视分级补全】开始识别 NFO 元数据，总数：N
```

识别进度日志：

```text
【影视分级补全】NFO 识别中，已识别：X/N，已有分级：A，无ID错误：B，待处理：C
```

识别进度输出频率：

```text
每 5 秒输出一次
```

识别结束日志：

```text
【影视分级补全】NFO 识别完成，总数：N，已有分级：A，无ID错误：B，待处理：C，解析失败：D，耗时：X 秒
```

---

## 18. 分级处理日志

分级处理阶段只处理待处理队列中的 NFO。

### 18.1 待处理数量小于 100

如果待处理 NFO 文件数量小于 100 条，每条处理记录都输出过程日志。

单条开始日志：

```text
【影视分级补全】开始处理：标题={title}，imdbid={imdbid}，tmdbid={tmdbid}
```

单条 OMDb 日志：

```text
【影视分级补全】OMDb 查询：{imdbid}
```

单条 TMDb 日志：

```text
【影视分级补全】TMDb 查询：类型={media_type}，tmdbid={tmdbid}
```

单条成功日志：

```text
【影视分级补全】处理成功：标题={title}，分级={rating}，来源={source}
```

单条失败日志：

```text
【影视分级补全】处理失败：标题={title}，原因={error}
```

### 18.2 待处理数量大于等于 100

如果待处理 NFO 文件数量大于等于 100 条，不逐条输出详细过程，按 5 秒间隔输出整体进度。

处理进度日志：

```text
【影视分级补全】分级处理中，成功：S，失败：F，跳过：K，已处理：X/N
```

处理进度输出频率：

```text
每 5 秒输出一次
```

### 18.3 处理开始与结束日志

处理开始日志：

```text
【影视分级补全】开始补充分级，待处理 NFO：N
```

处理结束日志：

```text
【影视分级补全】分级补全完成，总数：N，OMDb成功：A，TMDb成功：B，大陆兜底：C，其他兜底：D，失败：E，耗时：X 秒
```

---

## 19. 处理状态

| 状态                | 说明                  |
| ------------------- | --------------------- |
| scanned             | 已扫描到 NFO          |
| skipped_existing    | 已有分级，跳过        |
| queued              | 已加入待处理队列      |
| updated_omdb        | OMDb 查询成功并写入   |
| updated_tmdb        | TMDb 查询成功并写入   |
| fallback_mainland   | 大陆地区兜底写入      |
| fallback_other      | 其他地区兜底写入      |
| no_imdbid_no_tmdbid | 无 imdbid 且无 tmdbid |
| api_limit           | API 调用达到限额      |
| api_error           | API 请求失败          |
| parse_error         | NFO 解析失败          |
| write_error         | NFO 写入失败          |
| manual_updated      | 手动修改分级成功      |
| manual_failed       | 手动修改分级失败      |

---

## 20. API 调用计数

计入 API 次数：

```text
OMDb 实际请求
TMDb 实际请求
```

不计入 API 次数：

```text
已有分级跳过
无 imdbid / tmdbid 跳过
命中本地缓存
```

---

## 21. 主流程

### 21.1 总体流程

```text
开始
↓
读取配置
↓
扫描所有媒体库文件
↓
按照 NFO 识别规则获取全部主 NFO 文件
↓
输出总 NFO 文件数
↓
识别所有 NFO 的 title / imdbid / tmdbid / country / rating/mpaa
↓
检查是否已有有效分级
├─ 已有分级：记录 skipped_existing
└─ 无分级：继续检查 ID
↓
检查是否有 imdbid 或 tmdbid
├─ 都没有：输出错误信息，记录 no_imdbid_no_tmdbid
└─ 至少有一个：加入待处理队列
↓
输出识别汇总
↓
处理待处理队列
↓
有 imdbid？
├─ 是：查询 OMDb
│   ├─ 有 Rated：写入 NFO，记录 updated_omdb
│   └─ 无 Rated：继续 TMDb
└─ 否：继续 TMDb
↓
有 tmdbid？
├─ 是：按类型查询 TMDb
│   ├─ 有分级：写入 NFO，记录 updated_tmdb
│   └─ 无分级：进入地区兜底
└─ 否：进入地区兜底
↓
判断地区
├─ 大陆：写入 PG-13，记录 fallback_mainland
└─ 其他：写入 R，记录 fallback_other
↓
输出处理汇总
↓
结束
```

### 21.2 阶段拆分

主流程分为三个阶段：

```text
阶段一：扫描阶段
只负责遍历媒体库，识别 NFO 文件，不解析具体内容。

阶段二：识别阶段
解析 NFO，读取标题、年份、imdbid、tmdbid、国家地区、已有分级，并生成待处理队列。

阶段三：处理阶段
只处理无分级且有 imdbid/tmdbid 的记录，执行 OMDb/TMDb 查询、兜底和写回。
```

这样做的好处：

```text
扫描数量清晰
错误分类清晰
待处理数量清晰
API 调用更可控
日志进度更明确
页面记录更完整
```

---

## 22. 推荐插件目录结构

```text
plugins.v2/mediaratingfiller/
  __init__.py
  plugin.py
  scanner.py
  nfo.py
  omdb.py
  tmdb.py
  storage.py
  models.py
  utils.py
  README.md
```

| 文件       | 作用                                 |
| ---------- | ------------------------------------ |
| plugin.py  | 插件入口、配置、页面、任务、日志调度 |
| scanner.py | 扫描媒体库和识别主 NFO               |
| nfo.py     | 解析和写入 NFO                       |
| omdb.py    | OMDb 查询                            |
| tmdb.py    | TMDb 查询                            |
| storage.py | SQLite 记录、缓存、筛选查询          |
| models.py  | 数据结构                             |
| utils.py   | 路径、时间、日志节流等通用工具       |

---

## 23. 一期范围

一期实现：

```text
多媒体库路径配置
排除目录配置
OMDb API Key
TMDb API Key
API 调用限额
电影名.nfo 识别
tvshow.nfo 识别
imdbid / tmdbid 读取
已有分级判断
全量扫描后输出总 NFO 数
识别阶段生成待处理队列
OMDb 查询
TMDb 查询
地区兜底
NFO 写回
SQLite 历史记录
历史记录组合筛选
清空历史记录
历史记录手动修改分级
接入 MoviePilot 日志系统
扫描/识别/处理阶段进度日志
```

暂不实现：

```text
国家分级转换
自动刷新 Emby/Jellyfin
逐集 NFO 处理
复杂权限控制
批量手动修改
```

---

## 24. 一句话总结

```text
该插件用于补全已入库影视 NFO 的分级字段，先全量扫描和识别 NFO，再对缺失分级且具备 imdbid/tmdbid 的记录执行 OMDb/TMDb 查询和地区兜底，并支持历史筛选、清空记录、手动修改分级和 MoviePilot 日志进度输出。
```