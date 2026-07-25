#!/usr/bin/env python3
"""
MGSV 数据集字段迁移脚本 V1 → V2

变更内容：
  删除: music_type, genre, emotion_coarse, emotion_fine, scene_category, scene_atmosphere
  新增: vocal_presence（自动检测）, emotion, style, usage_scene（空白，待人工标注）

运行：
  conda activate mgsv_data
  cd E:\\MGSV_preprocessor
  python migrate_dataset_v2.py
"""

import os, warnings
warnings.filterwarnings("ignore")

import librosa
import numpy as np
import pandas as pd

EXCEL      = r"E:\MGSV_preprocessor\outputs\MGSV_Master_Dataset.xlsx"
SCAN_ROOT  = r"E:\MGSV_preprocessor\DouK-Source\Volume\Download"

# ── 标注字段说明（打印给标注者参考）──────────────────────────
LABEL_GUIDE = """
人工标注字段说明（多选，用 / 分隔）：

  emotion     热血 / 欢乐 / 浪漫 / 伤感 / 孤独 / 治愈 / 放松 / 平静 / 紧张 / 悬疑
  style       青春 / 回忆 / 高级感 / 科技感 / 史诗感 / 梦幻 / 国风 / 二次元 / 旅行 / 文艺 / 卡点 / 潮流
  usage_scene 学习 / 运动 / 开车 / 夜晚 / 散步 / 旅行 / 探店 / 美食 / 宠物 / 风景 / 剧情

vocal_presence（自动检测，可人工修正）：
  None     全程无人声（纯音乐 / 前奏）
  Partial  部分有人声（前奏+首句 / 间奏混排）
  Full     持续有人声（主歌 / 副歌段）
"""


def run_vocal_presence(music_path, chunk_sec=4):
    """时序检测人声存在程度，分析对象为视频绑定音频片段。"""
    try:
        y, sr = librosa.load(music_path, sr=22050)
        chunk_len   = chunk_sec * sr
        n_chunks    = max(1, len(y) // chunk_len)
        vocal_flags = []

        for i in range(n_chunks):
            chunk = y[i * chunk_len: (i + 1) * chunk_len]
            if len(chunk) < sr:
                continue
            y_h   = librosa.effects.harmonic(chunk, margin=3.0)
            S     = np.abs(librosa.stft(y_h, n_fft=2048, hop_length=512))
            freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)

            vocal_band   = (freqs >= 300) & (freqs <= 3400)
            vocal_energy = float(S[vocal_band, :].mean())
            total_energy = float(S.mean())
            vocal_ratio  = vocal_energy / (total_energy + 1e-8)

            contrast      = librosa.feature.spectral_contrast(S=S, sr=sr)
            mean_contrast = float(contrast.mean())

            score = vocal_ratio * 0.7 + min(mean_contrast / 30.0, 1.0) * 0.3
            vocal_flags.append(score > 0.45)

        if not vocal_flags:
            return ''
        vocal_rate = sum(vocal_flags) / len(vocal_flags)
        if vocal_rate < 0.20:
            return 'None'
        elif vocal_rate >= 0.70:
            return 'Full'
        else:
            return 'Partial'
    except Exception as e:
        print(f"    ⚠️ vocal_presence 检测失败: {e}")
        return ''


def find_audio_for_video(v_file, scan_root):
    """在 scan_root 下找与 v_file 同目录的音频文件。"""
    for root, _, files in os.walk(scan_root):
        if v_file in files:
            audio_files = [f for f in files
                           if f.lower().endswith(('.m4a', '.mp3', '.wav'))]
            if audio_files:
                return os.path.join(root, audio_files[0])
    return None


def main():
    print(LABEL_GUIDE)

    if not os.path.exists(EXCEL):
        print(f"❌ 找不到数据集文件：{EXCEL}")
        return

    df = pd.read_excel(EXCEL)
    print(f"📊 加载数据集：{len(df)} 条记录，字段：{df.columns.tolist()}\n")

    # ── 1. 删除旧字段 ──────────────────────────────────────────
    old_cols = ['music_type', 'genre', 'emotion_coarse', 'emotion_fine',
                'scene_category', 'scene_atmosphere']
    removed = [c for c in old_cols if c in df.columns]
    df = df.drop(columns=removed)
    if removed:
        print(f"🗑️  已删除旧字段：{removed}")

    # ── 2. 插入新字段（sync_level 之后）────────────────────────
    insert_after = 'sync_level'
    insert_pos   = df.columns.get_loc(insert_after) + 1 if insert_after in df.columns else len(df.columns)

    for field in ['vocal_presence', 'emotion', 'style', 'usage_scene', 'rhythm_points']:
        if field not in df.columns:
            df.insert(insert_pos, field, '')
            insert_pos += 1

    print("✅ 新字段已插入：vocal_presence / emotion / style / usage_scene\n")

    # ── 3. 补跑 vocal_presence ─────────────────────────────────
    print("🎵 开始检测 vocal_presence（基于视频绑定音频）...\n")
    for idx, row in df.iterrows():
        v_file = row['video_id']
        # 若已有值则跳过（防止覆盖人工修正）
        if pd.notna(row.get('vocal_presence')) and str(row.get('vocal_presence')).strip():
            print(f"  [{idx+1}] {v_file[:45]} → 已有值 '{row['vocal_presence']}'，跳过")
            continue

        music_path = find_audio_for_video(v_file, SCAN_ROOT)
        if not music_path:
            print(f"  [{idx+1}] {v_file[:45]} → ⚠️ 未找到音频，跳过")
            continue

        result = run_vocal_presence(music_path)
        df.at[idx, 'vocal_presence'] = result
        print(f"  [{idx+1}] {v_file[:45]} → {result}")

    # ── 4. 保存 ────────────────────────────────────────────────
    df.to_excel(EXCEL, index=False)
    print(f"\n✅ 迁移完成，已保存：{EXCEL}")
    print(f"   字段顺序：{df.columns.tolist()}")
    print("\n📝 请在 Excel 中手动填写 emotion / style / usage_scene 列。")
    print("   多个标签用 / 分隔，例如：治愈/放松")


if __name__ == "__main__":
    main()
