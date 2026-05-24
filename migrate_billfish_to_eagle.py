"""
Billfish -> Eagle 迁移脚本
仅迁移打了标签或有评分的素材（含标签/评分/备注）。
文件系统直接写入方案。Eagle 开启后会自动扫描新文件。

用法:
    python migrate_billfish_to_eagle.py

依赖: 仅标准库 (sqlite3, json, shutil, pathlib)
"""
import sqlite3
import json
import shutil
import time
import os
import random
import string
from pathlib import Path

# ============================================================
# 配置区
# ============================================================
MIGRATIONS = [
    {
        "bf_db":   r"E:\本地资源库\.bf\billfish.db",
        "bf_root": r"E:\本地资源库",
        "eagle_lib_name": "eagle—绘画.library",
        "label": "绘画",
    },
    {
        "bf_db":   r"E:\摄影\.bf\billfish.db",
        "bf_root": r"E:\摄影",
        "eagle_lib_name": "eagle—摄影.library",
        "label": "摄影",
    },
]


def find_eagle_lib(dir_name):
    """在 E:/ 下按名称匹配 Eagle 库（处理 emdash 等特殊字符）"""
    parent = "E:\\"
    name_base = dir_name.replace(".library", "")
    for entry in os.listdir(parent):
        if entry.endswith(".library"):
            # 用关键词匹配
            entry_base = entry.replace(".library", "")
            if "绘画" in entry_base and "绘画" in name_base:
                return os.path.join(parent, entry)
            if "摄影" in entry_base and "摄影" in name_base:
                return os.path.join(parent, entry)
    # fallback: 按 library 名精确匹配
    candidate = os.path.join(parent, dir_name)
    return candidate if os.path.exists(candidate) else None
# 保持 Billfish 标签层级: "父/子" 格式 + 每级保留独立父标签


def id_generator():
    """生成 13 位大写字母数字 ID（模仿 Eagle 格式）"""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=13))


def sanitize_text(s):
    """清洗文本中的代理字符"""
    if not s:
        return ""
    return str(s).encode("utf-8", errors="replace").decode("utf-8")


def read_billfish(db_path, lib_root):
    """
    读取 Billfish 数据库，返回需迁移的素材列表。
    过滤: 有标签 OR 有评分(score>0)
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # ---- 文件夹层级 ----
    c.execute("SELECT id, name, pid FROM bf_folder")
    folder_map = {r["id"]: (r["name"], r["pid"]) for r in c.fetchall()}

    def get_folder_path(folder_id):
        if not folder_id or folder_id == 0:
            return ""
        parts = []
        fid = folder_id
        seen = set()
        while fid and fid != 0 and fid not in seen:
            if fid not in folder_map:
                break
            seen.add(fid)
            name, pid = folder_map[fid]
            parts.append(name)
            fid = pid
        parts.reverse()
        return "/".join(parts)

    # ---- 标签层级（bf_tag_v2） ----
    c.execute("SELECT id, name, pid FROM bf_tag_v2")
    tag_rows = c.fetchall()
    tag_pid = {}
    tag_name = {}
    for r in tag_rows:
        tag_pid[r["id"]] = r["pid"]
        tag_name[r["id"]] = r["name"]

    def get_tag_full_paths(tag_id):
        """返回标签的所有层级路径: ['父', '父/子', '父/子/孙']"""
        paths = []
        cur = tag_id
        parts = []
        seen = set()
        while cur and cur != 0 and cur not in seen:
            if cur not in tag_name:
                break
            seen.add(cur)
            parts.append(sanitize_text(tag_name[cur]))
            cur = tag_pid.get(cur, 0)

        full_parts = list(reversed(parts))
        for i in range(len(full_parts)):
            paths.append("/".join(full_parts[: i + 1]))
        return paths

    # ---- 主查询：过滤有标签或有评分的文件 ----
    # 注意：使用 DISTINCT 因为 LEFT JOIN 标签关联表会产生重复行
    c.execute("""
        SELECT DISTINCT f.id, f.name, f.pid AS folder_id,
               f.file_size, f.born, f.md5
        FROM bf_file f
        LEFT JOIN bf_material_userdata u ON u.file_id = f.id
        LEFT JOIN bf_tag_join_file t ON t.file_id = f.id
        WHERE (u.score IS NOT NULL AND u.score > 0)
           OR t.tag_id IS NOT NULL
        ORDER BY f.id
    """)
    file_rows = c.fetchall()

    # ---- 获取评分和备注 ----
    c.execute("SELECT file_id, score, note, origin FROM bf_material_userdata")
    udata_map = {}
    for r in c.fetchall():
        udata_map[r["file_id"]] = {
            "score": r["score"] or 0,
            "note": sanitize_text(r["note"] or ""),
            "origin": sanitize_text(r["origin"] or ""),
        }

    # ---- 获取文件标签 ----
    c.execute("SELECT file_id, tag_id FROM bf_tag_join_file")
    file_tags = {}
    for r in c.fetchall():
        fid = r["file_id"]
        tid = r["tag_id"]
        if fid not in file_tags:
            file_tags[fid] = set()
        file_tags[fid].add(tid)

    # ---- 构建输出 ----
    items = []
    for row in file_rows:
        fid = row["id"]
        fname = row["name"]
        folder_path = get_folder_path(row["folder_id"])
        filesize = row["file_size"] or 0
        born = row["born"]

        ud = udata_map.get(fid, {"score": 0, "note": "", "origin": ""})
        score = ud["score"]
        note = ud["note"]
        origin = ud["origin"]

        # 构建完整文件路径
        if folder_path:
            abs_path = os.path.join(lib_root, folder_path, fname)
            rel_path = folder_path + "/" + fname
        else:
            abs_path = os.path.join(lib_root, fname)
            rel_path = fname

        # 检查文件是否真的存在
        if not os.path.exists(abs_path):
            # 尝试直接在根目录找
            alt_path = os.path.join(lib_root, fname)
            if os.path.exists(alt_path):
                abs_path = alt_path
                rel_path = fname
            else:
                print(f"  [SKIP] 文件不存在: {abs_path}")
                continue

        # 标签处理
        tags = []
        if fid in file_tags:
            for tid in file_tags[fid]:
                paths = get_tag_full_paths(tid)
                tags.extend(paths)
        tags = sorted(set(tags))  # 去重排序

        # 来源 URL（仅当 origin 是有效 URL 时使用）
        url = ""
        if origin and str(origin).startswith("http"):
            url = str(origin)

        items.append(
            {
                "file_id": fid,
                "filename": fname,
                "abs_path": abs_path,
                "rel_path": rel_path,
                "size": filesize,
                "score": score,
                "tags": tags,
                "note": note,
                "url": url,
            }
        )

    conn.close()
    return items


def import_to_eagle(items, eagle_lib, label):
    """将素材列表写入 Eagle 库文件系统"""
    images_dir = os.path.join(eagle_lib, "images")
    os.makedirs(images_dir, exist_ok=True)

    now_ms = int(time.time() * 1000)
    total = len(items)
    success = 0
    skipped = 0
    errors = 0

    print(f"\n[{label}] 开始迁移 {total} 条素材 -> {eagle_lib}")

    for i, item in enumerate(items):
        try:
            # 生成 Eagle ID（确保不碰撞）
            eid = id_generator()
            info_dir = os.path.join(images_dir, f"{eid}.info")
            while os.path.exists(info_dir):
                eid = id_generator()
                info_dir = os.path.join(images_dir, f"{eid}.info")

            os.makedirs(info_dir, exist_ok=False)

            # 分离文件名和扩展名
            name_no_ext, ext = os.path.splitext(item["filename"])
            ext = ext.lstrip(".").lower()
            if not ext:
                ext = "jpg"  # 兜底

            # 复制文件
            dest_file = os.path.join(info_dir, item["filename"])
            shutil.copy2(item["abs_path"], dest_file)
            actual_size = os.path.getsize(dest_file)

            # 构建 metadata.json
            meta = {
                "id": eid,
                "name": name_no_ext,
                "size": actual_size,
                "btime": now_ms,
                "mtime": now_ms,
                "ext": ext,
                "tags": item["tags"],
                "folders": [],
                "isDeleted": False,
                "url": item["url"],
                "annotation": item["note"],
                "modificationTime": now_ms,
                "width": 0,
                "height": 0,
                "noThumbnail": False,
                "lastModified": now_ms,
                "star": item["score"] if item["score"] > 0 else None,
                "palettes": [],
            }

            meta_path = os.path.join(info_dir, "metadata.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            success += 1
            if (i + 1) % 100 == 0 or (i + 1) == total:
                print(f"  [{label}] {i + 1}/{total} ({success} ok, {skipped} skip, {errors} err)")

        except Exception as e:
            errors += 1
            print(f"  [{label}] ERROR #{item['file_id']}: {e}")

    print(f"\n[{label}] 完成: 成功 {success}, 跳过 {skipped}, 失败 {errors} / 共 {total}")

    return {"success": success, "skipped": skipped, "errors": errors, "total": total}


def main():
    print("=" * 50)
    print("Billfish -> Eagle 迁移脚本")
    print("=" * 50)

    for mig in MIGRATIONS:
        db_path = mig["bf_db"]
        lib_root = mig["bf_root"]
        eagle_lib_name = mig["eagle_lib_name"]
        label = mig["label"]

        # 验证 DB 存在
        if not os.path.exists(db_path):
            print(f"\n[SKIP] {label}: 数据库不存在 {db_path}")
            continue

        # 查找 Eagle 库路径
        eagle_lib = find_eagle_lib(eagle_lib_name)
        if not eagle_lib or not os.path.exists(eagle_lib):
            print(f"\n[SKIP] {label}: Eagle 库不存在 ({eagle_lib_name})，请先创建")
            continue

        # 读取 Billfish
        print(f"\n[{label}] 读取 Billfish 数据库: {db_path}")
        items = read_billfish(db_path, lib_root)
        print(f"[{label}] 需迁移 {len(items)} 条素材")

        if not items:
            print(f"[{label}] 无数据需要迁移")
            continue

        # 写入 Eagle
        result = import_to_eagle(items, eagle_lib, label)

    print("\n" + "=" * 50)
    print("迁移全部完成。请打开 Eagle 客户端查看结果。")
    print("注意: 评分(star)已写入 metadata.json，Eagle 4.0+ 应能识别。")
    print("如评分未显示，请开启 Eagle 后运行 python apply_stars.py 通过 API 补充。")
    print("=" * 50)


if __name__ == "__main__":
    main()
