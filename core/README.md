# core/ · 共享底层

所有口径都要用的基础模块。**不要在这里放业务判定逻辑。**

| 文件 | 作用 |
|---|---|
| `datahub.py` | 日线三级灾备取数（akshare → tushare → 网页）；`is_index_code()` 按前缀判指数 |
| `minute_hub.py` | 60m 分钟线（`kind="index"` / `"stock"`）；⚠️ 磁盘缓存 key 不含 `n`，长度不够要 `cache=False` |
| `tushare_init.py` | Tushare 统一初始化（代理 token + 硬超时 + 直连兜底） |
| `feishu_card.py` | 飞书 interactive 卡片合成（文字分节 + 内嵌图） |
| `push_image.py` | 统一图片推送（QQ 需先落到 `~/.openclaw/media/`，否则静默失败） |

## 约定

- 每个文件都自带 `_repo_root()` + `sys.path` 引导，可在任意层级被 import。
- 新增共享能力放这里；只在单一方向用的，放对应目录。
