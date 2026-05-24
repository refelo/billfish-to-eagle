"""
Eagle 评分补充脚本（备用）
如果 Eagle 打开后评分未自动显示，运行此脚本通过 API 批量写入。
前提：Eagle App 已打开并切到目标库。
用法：
    python apply_stars.py
依赖: 仅标准库
"""
import os, json, time, urllib.request, urllib.error

EAGLE_API = "http://localhost:41595"


def find_eagle_lib(lib_name):
    """在 E:/ 下按名称匹配 Eagle 库"""
    parent = "E:\\"
    name_base = lib_name.replace(".library", "").replace("\u2014", "").replace("-", "")
    for entry in os.listdir(parent):
        if entry.endswith(".library"):
            entry_base = entry.replace(".library", "").replace("\u2014", "").replace("-", "")
            if name_base in entry_base or entry_base in name_base:
                return os.path.join(parent, entry)
    return None


def apply_stars(lib_name):
    """为指定 Eagle 库中所有有 star 字段的 metadata.json 通过 API 写入评分"""
    lib_path = find_eagle_lib(lib_name)
    if not lib_path:
        print(f"未找到库: {lib_name}")
        return

    images_dir = os.path.join(lib_path, "images")
    if not os.path.exists(images_dir):
        print(f"无 images 目录: {lib_path}")
        return

    infos = sorted([d for d in os.listdir(images_dir) if d.endswith(".info")])
    total = len(infos)
    processed = 0
    failures = 0

    print(f"[{lib_name}] 扫描 {total} 条...")

    for info_name in infos:
        meta_path = os.path.join(images_dir, info_name, "metadata.json")
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        star = meta.get("star")
        eid = meta.get("id")
        if not star or not eid or star <= 0:
            continue

        try:
            data = json.dumps({"id": eid, "star": star}).encode("utf-8")
            req = urllib.request.Request(
                f"{EAGLE_API}/api/item/update",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=10)
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("status") == "success":
                processed += 1
                del meta["star"]
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
            else:
                failures += 1
        except Exception as e:
            failures += 1
            print(f"  错误: {e}")
            break

        if processed % 100 == 0 and processed > 0:
            print(f"  [{lib_name}] {processed} stars applied, {failures} failed")

        time.sleep(0.05)

    print(f"[{lib_name}] 完成: {processed} 条评分写入, {failures} 失败 / 共 {total}")


if __name__ == "__main__":
    print("Eagle 评分补充脚本")
    print("确保 Eagle App 已打开且切换到目标库\n")
    apply_stars("eagle—绘画.library")
    print()
    apply_stars("eagle—摄影.library")
    print("\n完成。")
