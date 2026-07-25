#!/usr/bin/env python3
"""分镜点检测：TransNetV2 → 全部转场点 + 双方案均分选点 → 写入数据表

用法:
    python shot_detect.py            # 只处理 shot_points 为空的行
    python shot_detect.py --force    # 全部重新检测

列约定（'/' 分隔，绝对秒数，升序）:
    shot_points    检测到的全部有效转场点；'NONE' = 已检测但无；'' = 尚未检测
    shot_points_3  最接近 4 等分位置的 3 个点（不足 3 个则全用）
    shot_points_5  最接近 6 等分位置的 5 个点；与方案3完全相同时记 'SAME'

打分列（由标注工具写入）:
    seg_scores_3   方案A（top-3 / 自适应 / 非卡点整段）各段 1-5 分
    seg_scores_5   方案B（top-5）各段 1-5 分；无方案B时为空
"""
import os, shutil, argparse
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')

import numpy as np
import pandas as pd
import cv2

EXCEL      = r"E:\MGSV_preprocessor\outputs\MGSV_Master_Dataset.xlsx"
SCAN_ROOT  = r"E:\MGSV_preprocessor\DouK-Source\Volume\Download"

MIN_CONF   = 0.5    # 转场概率下限
EDGE_PAD   = 0.5    # 距音乐段起止的保护间隔（秒）


def build_video_index(root):
    idx = {}
    if not os.path.exists(root):
        return idx
    for r, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(('.mp4', '.avi', '.mov')):
                idx[f] = os.path.join(r, f)
    return idx


def video_duration(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    n   = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return (n / fps) if fps > 0 else 0.0


def detect_cuts(video_path, model):
    """返回 [(秒, 置信度), ...]"""
    _, single, _ = model.predict_video(video_path)
    scenes = model.predictions_to_scenes(single)
    if len(scenes) <= 1:
        return []
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    if not fps or fps <= 0:
        fps = 30.0
    cuts = []
    for start in scenes[1:, 0]:
        lo = max(0, int(start) - 3)
        hi = min(len(single), int(start) + 3)
        conf = float(np.max(single[lo:hi]))
        cuts.append((float(start) / fps, conf))
    return cuts


def select_even(pts, seg_start, seg_end, n):
    """从升序点列表中选最接近 (n+1) 等分目标位置的 n 个点，返回升序列表。
    点数 <= n 时全部返回（自适应）。"""
    if len(pts) <= n:
        return sorted(pts)
    L = seg_end - seg_start
    targets = [seg_start + (k + 1) * L / (n + 1) for k in range(n)]
    used, out = set(), []
    for t in targets:
        best, bd = None, None
        for p in pts:
            if p in used:
                continue
            d = abs(p - t)
            if bd is None or d < bd:
                bd, best = d, p
        if best is not None:
            used.add(best)
            out.append(best)
    return sorted(out)


def fmt(pts):
    return '/'.join(f"{t:.2f}" for t in pts)


def save_excel(df):
    """原子保存：写 .tmp → 备份旧版 → 替换，避免中断截断 Excel"""
    tmp = os.path.splitext(EXCEL)[0] + '.tmp.xlsx'
    df.to_excel(tmp, index=False)
    try:
        if os.path.exists(EXCEL):
            shutil.copy2(EXCEL, os.path.splitext(EXCEL)[0] + '.backup.xlsx')
    except Exception as e:
        print(f"备份失败(不影响保存): {e}")
    os.replace(tmp, EXCEL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true', help='重新检测所有行')
    ap.add_argument('--row', type=int, default=None, help='只重新检测指定 Excel 行号')
    ap.add_argument('--min-conf', type=float, default=MIN_CONF,
                    help=f'转场置信度阈值，默认 {MIN_CONF}，漏检时可试 0.35')
    args = ap.parse_args()

    print("正在加载 TransNetV2 ...")
    from transnetv2 import TransNetV2
    model = TransNetV2()

    df = pd.read_excel(EXCEL)
    for col in ('shot_points', 'shot_points_3', 'shot_points_5'):
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].astype(object)

    vindex = build_video_index(SCAN_ROOT)
    print(f"索引到 {len(vindex)} 个视频，共 {len(df)} 行")

    done = skip = fail = 0
    for i, row in df.iterrows():
        if args.row is not None and i != args.row:
            skip += 1
            continue
        cur = str(row.get('shot_points', '') or '').strip()
        if cur and cur.lower() != 'nan' and not args.force and args.row is None:
            skip += 1
            continue
        vid = str(row.get('video_id', '') or '')
        vp  = vindex.get(vid)
        if not vp:
            print(f"[{i}] ⚠ 找不到视频: {vid}")
            fail += 1
            continue
        ms = 0.0
        try:
            me = float(row.get('video_total_duration') or 0)
        except (TypeError, ValueError):
            me = 0.0
        if me <= 0:
            me = video_duration(vp)
        if me <= 0:
            print(f"[{i}] ⚠ 无法确定视频范围: {vid}")
            fail += 1
            continue

        print(f"[{i}] {vid}  视频段 {ms:.1f}–{me:.1f}s")
        try:
            cuts = detect_cuts(vp, model)
        except Exception as e:
            print(f"[{i}] ❌ 检测失败: {e}")
            fail += 1
            continue

        pts = sorted(t for t, c in cuts
                     if ms + EDGE_PAD < t < me - EDGE_PAD and c >= args.min_conf)
        if not pts:
            df.at[i, 'shot_points']   = 'NONE'
            df.at[i, 'shot_points_3'] = 'NONE'
            df.at[i, 'shot_points_5'] = 'NONE'
            print("      → 无有效分镜点")
        else:
            p3 = select_even(pts, ms, me, 3)
            p5 = select_even(pts, ms, me, 5)
            df.at[i, 'shot_points']   = fmt(pts)
            df.at[i, 'shot_points_3'] = fmt(p3)
            df.at[i, 'shot_points_5'] = 'SAME' if p5 == p3 else fmt(p5)
            print(f"      → 全部 {len(pts)} 点 | 方案3: {fmt(p3)} | 方案5: "
                  f"{'SAME' if p5 == p3 else fmt(p5)}")
        save_excel(df)                    # 每行原子落盘，中断不丢
        done += 1

    print(f"\n完成 {done} | 跳过 {skip} | 失败 {fail}")


if __name__ == '__main__':
    main()
