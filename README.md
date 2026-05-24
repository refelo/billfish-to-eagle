# Billfish → Eagle 迁移工具

将 Billfish 素材管理器中的图片迁移到 Eagle，完整保留**标签、评分（星级）、备注**。

## 功能

- **GUI 界面**（`billfish2eagle_gui.py`）— 双击运行，可视化操作
- **命令行**（`migrate_billfish_to_eagle.py`）— 适合自动化 / 批量
- **API 补写**（`apply_stars.py`）— 若评分未显示，通过 Eagle API 补写

### GUI 功能

| 功能 | 说明 |
|------|------|
| 路径选择 | Billfish 数据库 / 资源库根目录 / Eagle 库，支持浏览按钮 |
| 导出选择 | 标签 / 评分(星级) / 备注，可独立开关 |
| 筛选模式 | 有标签或有评分 / 仅标签 / 仅评分 / 全部 / 指定标签(交集) |
| 标签层级 | 保留父标签路径（如 `人物/神态`），可关闭 |
| 断点续传 | 中断后可从上次位置继续 |
| 进度显示 | 实时进度条 + 成功/失败计数 |
| 磁盘预检 | 迁移前检查目标盘剩余空间 |
| WAL 安全 | 自动处理 Billfish 的 WAL 数据库模式 |

## 安装

只需 Python 3.8+，**零第三方依赖**（所有脚本仅使用标准库）。

```bash
# 下载
git clone https://github.com/<your-username>/billfish-to-eagle.git
cd billfish-to-eagle

# GUI 方式（推荐）
python billfish2eagle_gui.py
```

## 使用步骤

### GUI

1. 双击 `billfish2eagle_gui.py` 或 `python billfish2eagle_gui.py`
2. 选择 Billfish 数据库（通常在 `资源库\.bf\billfish.db`）
3. 选择 Billfish 资源库根目录
4. 选择 Eagle 库路径（`*.library` 文件夹）
5. 勾选要导出的数据（标签/评分/备注）
6. 选择筛选条件
7. 点击「开始迁移」

### CLI

编辑 `migrate_billfish_to_eagle.py` 顶部的 `MIGRATIONS` 配置，然后：

```bash
python migrate_billfish_to_eagle.py
```

### 评分补写

如果 Eagle 中评分未自动显示：

```bash
# 先打开 Eagle App（必须）
python apply_stars.py
```

## 迁移原理

```
Billfish (SQLite)                Eagle (文件系统)
┌─────────────────┐              ┌──────────────────────┐
│ billfish.db      │              │ *.library/            │
│  ├ bf_file       │──读取──▶    │  ├ images/            │
│  ├ bf_tag_v2     │              │  │  ├ ABC123.info/    │
│  ├ bf_tag_join   │              │  │  │  ├ image.jpg    │
│  └ bf_userdata   │              │  │  │  └ metadata.json│
└─────────────────┘              │  │  └ ...             │
                                 │  └ ...                │
                                 └──────────────────────┘
```

## 注意事项

- 迁移前请**备份** Billfish 和 Eagle 库
- Eagle 库需先在 Eagle 中创建（空库即可）
- Billfish 的文件不会被修改或删除
- 标签层级默认展平为 `父/子` 格式，可在 GUI 中关闭
- 评分写入 `metadata.json` 的 `star` 字段，Eagle 4.0+ 会自动识别

## License

MIT
