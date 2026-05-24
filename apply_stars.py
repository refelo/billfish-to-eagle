"""
Eagle 评分补充脚本（备用）
如果 Eagle 打开后评分未自动显示，运行此脚本通过 API 批量写入。
前提：Eagle App 已打开并切到目标库。
用法：
    python apply_stars.py
"""
import os, json, requests, time

EAGLE_API = "http://localhost:41595"


def apply_stars(lib_name):
    """为指定 Eagle 库中所有有 star 字段的 metadata.json 通过 API 写入评分"""
    parent = "E:\\"
    lib_path = None
    for entry in os.listdir(parent):
        if entry.endswith(".library"):
            ek = entry.replace(".library", "")
            nk = lib_name.replace(".library", "")
            for c in nk:
                if c in ek and len(c) > 1:
                    lib_path = os.path.join(parent, entry)
                    break
        if lib_path:
            break
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
            resp = requests.post(
                f"{EAGLE_API}/api/item/update",
                json={"id": eid, "star": star},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    processed += 1
                    # 从 metadata.json 中移除 star 字段（避免重复处理）
                    del meta["star"]
                    with open(meta_path, "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False, indent=2)
                else:
                    failures += 1
            else:
                failures += 1
        except Exception as e:
            failures += 1
            print(f"  错误: {e}")
            break

        if processed % 100 == 0:
            print(f"  [{lib_name}] {processed} stars applied, {failures} failed")

        time.sleep(0.05)  # 避免压垮 API

    print(f"[{lib_name}] 完成: {processed} 条评分写入, {failures} 失败 / 共 {total}")


if __name__ == "__main__":
    print("Eagle 评分补充脚本")
    print("确保 Eagle App 已打开且切换到目标库\n")
    apply_stars("eagle—绘画.library")
    print()
    apply_stars("eagle—摄影.library")
    print("\n完成。")
