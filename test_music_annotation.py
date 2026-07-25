#!/usr/bin/env python3
"""
音乐标注方案对比测试
同时跑 Essentia + LLM (Claude API) + librosa 规则法 + HuggingFace genre 模型，
最后生成一张交叉验证对比 Excel。

运行环境：mgsv_data 或 douyin conda 环境
安装额外依赖（二选一）：
          # LLM 标注
  pip install essentia                                  # Python ≤3.11；Windows 可能需要 3.10
  pip install essentia-tensorflow                       # 可选：含情绪/流派 ML 模型

用法：
  conda activate mgsv_data
  cd E:\\MGSV_preprocessor
  python test_music_annotation.py
"""

import os, sys, json, re, warnings
warnings.filterwarnings("ignore")
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

# ============================================================
# ⚙️  配置区
# ============================================================
FULL_MUSIC_DIR = r"E:\MGSV_preprocessor\outputs\full_music"
DOUK_EXCEL     = r"E:\MGSV_preprocessor\DouK-Source\Volume\Data\Download.xlsx"
OUTPUT_EXCEL   = r"E:\MGSV_preprocessor\outputs\music_annotation_compare.xlsx"

# Anthropic API Key（或设环境变量 ANTHROPIC_API_KEY）
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL         = "claude-haiku-4-5-20251001"   # 便宜快速，适合批量标注

# ============================================================
# 🔍  依赖检查
# ============================================================
import importlib

def try_import(pkg):
    try:
        return importlib.import_module(pkg), True
    except ImportError:
        return None, False

_, HAS_LIBROSA       = try_import("librosa")
_, HAS_NUMPY         = try_import("numpy")
_, HAS_ESSENTIA      = try_import("essentia")
_, HAS_ESSENTIA_TF   = try_import("essentia.tensorflow") if HAS_ESSENTIA else (None, False)
_, HAS_ANTHROPIC     = try_import("anthropic")
_, HAS_TORCH         = try_import("torch")
_, HAS_TRANSFORMERS  = try_import("transformers")
_, HAS_PANDAS        = try_import("pandas")

if not HAS_PANDAS:
    print("❌ pandas 未安装，无法继续。请 pip install pandas openpyxl")
    sys.exit(1)

import pandas as pd

if HAS_LIBROSA:
    import librosa
    import numpy as np

if HAS_ESSENTIA:
    import essentia.standard as es

llm_client = None
if HAS_ANTHROPIC and ANTHROPIC_API_KEY:
    import anthropic
    llm_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

print("=" * 60)
print("依赖状态：")
print(f"  librosa        : {'✅' if HAS_LIBROSA else '❌ 未安装'}")
print(f"  essentia       : {'✅' if HAS_ESSENTIA else '❌ 未安装（pip install essentia）'}")
print(f"  essentia-tf    : {'✅' if HAS_ESSENTIA_TF else '⚠️  未安装（可选，含情绪ML模型）'}")
print(f"  anthropic      : {'✅' if HAS_ANTHROPIC else '❌ 未安装（pip install anthropic）'}")
print(f"  ANTHROPIC_KEY  : {'✅ 已设置' if ANTHROPIC_API_KEY else '❌ 未设置（需要才能跑LLM）'}")
print(f"  torch+transf.  : {'✅' if (HAS_TORCH and HAS_TRANSFORMERS) else '⚠️  未安装（HF genre模型将跳过）'}")
print("=" * 60)


# ============================================================
# 📂  读取 DouK 元数据
# ============================================================
def load_douk_meta():
    """返回 {stem: {title, author, url}} 映射，stem = 文件名去扩展名"""
    meta_map = {}
    if not os.path.exists(DOUK_EXCEL):
        print(f"⚠️ 未找到 DouK Excel: {DOUK_EXCEL}")
        return meta_map
    df = pd.read_excel(DOUK_EXCEL)
    for _, row in df.iterrows():
        title  = str(row.get('音乐标题', '')).strip()
        author = str(row.get('音乐作者', '')).strip()
        if title and title != 'nan':
            # 文件名 stem = title_author（safe）
            safe = re.sub(r'[\\/:*?"<>|]', '_', f"{title}_{author}")
            meta_map[safe] = {
                'music_title':  title,
                'music_author': author,
                'music_url':    str(row.get('音乐链接', '')).strip(),
                'video_desc':   str(row.get('作品描述', '')).strip(),
            }
            # 也用原始 key（非 safe）方便部分匹配
            meta_map[f"{title}_{author}"] = meta_map[safe]
    return meta_map


def parse_stem(filename):
    """'歌名_作者.mp3' → (title, author)"""
    stem = os.path.splitext(filename)[0]
    if '_' in stem:
        parts = stem.rsplit('_', 1)
        return parts[0], parts[1]
    return stem, ''


# ============================================================
# 🎵  分析器 1：librosa 规则法（仿 auto.py 逻辑）
# ============================================================
def analyze_librosa(audio_path):
    if not (HAS_LIBROSA and HAS_NUMPY):
        return {}
    try:
        y, sr = librosa.load(audio_path, sr=22050, duration=60)

        # BPM
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo[0]) if hasattr(tempo, '__len__') else float(tempo)

        # Energy
        rms = float(np.mean(librosa.feature.rms(y=y)))

        # Spectral centroid（亮度）
        centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
        centroid_norm = centroid / (sr / 2.0)

        # Key（chroma 重心）
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        key_idx = int(np.argmax(np.mean(chroma, axis=1)))
        key_name = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'][key_idx]

        # 节奏强度
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        rhythm_strength = float(onset_env.mean())

        # 人声估计（300-3400Hz 能量占比，来自 auto.py）
        stft  = np.abs(librosa.stft(y))
        freqs = librosa.fft_frequencies(sr=sr)
        vocal_mask   = (freqs >= 300) & (freqs <= 3400)
        vocal_score  = float(stft[vocal_mask].sum() / (stft.sum() + 1e-8))

        # Emotion（规则，来自 auto.py run_emotion_detection）
        energy_score = min(rms / 0.08, 1.0)
        tempo_score  = min(bpm / 160.0, 1.0)
        combined = energy_score * 0.5 + tempo_score * 0.3 + centroid_norm * 0.2

        if combined >= 0.55:
            emotion_coarse = 'Positive'
        elif combined <= 0.35:
            emotion_coarse = 'Negative'
        else:
            emotion_coarse = 'Neutral'

        emotion_fine = ''
        if emotion_coarse == 'Positive' and bpm > 120 and energy_score > 0.6:
            emotion_fine = 'Energetic'
        elif emotion_coarse == 'Neutral' and bpm < 80 and energy_score < 0.4:
            emotion_fine = 'Calm'

        # Danceability（简单启发：高 BPM + 强 onset）
        dance_score = min((bpm / 130.0) * 0.5 + (rhythm_strength / 4.0) * 0.5, 1.0)

        return {
            'lr_bpm':             round(bpm, 1),
            'lr_key':             key_name,
            'lr_rms_energy':      round(rms, 4),
            'lr_rhythm_strength': round(rhythm_strength, 3),
            'lr_vocal_score':     round(vocal_score, 3),
            'lr_music_type':      'Vocal' if vocal_score > 0.45 else 'Instrumental',
            'lr_emotion_coarse':  emotion_coarse,
            'lr_emotion_fine':    emotion_fine,
            'lr_combined_score':  round(combined, 3),
            'lr_dance_score':     round(dance_score, 3),
        }
    except Exception as e:
        print(f"    ⚠️ librosa 分析失败: {e}")
        return {}


# ============================================================
# 🎵  分析器 2：Essentia 综合特征
# ============================================================
def analyze_essentia(audio_path):
    if not HAS_ESSENTIA:
        return {}
    result = {}
    try:
        # MusicExtractor 一次性拿所有特征
        features, _ = es.MusicExtractor(
            lowlevelStats=['mean', 'stdev'],
            rhythmStats=['mean', 'stdev'],
            tonalStats=['mean', 'stdev'],
        )(audio_path)

        result['es_bpm']               = round(float(features['rhythm.bpm']), 1)
        result['es_key']               = features['tonal.key_key']
        result['es_scale']             = features['tonal.key_scale']   # major / minor
        result['es_key_strength']      = round(float(features['tonal.key_strength']), 3)
        result['es_danceability']      = round(float(features['rhythm.danceability']), 3)
        result['es_dynamic_complexity']= round(float(features['lowlevel.dynamic_complexity']), 3)
        result['es_loudness']          = round(float(features['lowlevel.average_loudness']), 3)
        result['es_spectral_rolloff']  = round(float(features['lowlevel.spectral_rolloff.mean']), 1)
        result['es_dissonance']        = round(float(features['lowlevel.dissonance.mean']), 4)
        result['es_pitch_salience']    = round(float(features['lowlevel.pitch_salience.mean']), 3)

    except Exception as e:
        print(f"    ⚠️ Essentia MusicExtractor 失败: {e}，尝试降级...")
        try:
            audio = es.MonoLoader(filename=audio_path, sampleRate=44100)()
            bpm_val, _, _, _, _ = es.RhythmExtractor2013()(audio)
            key_val, scale_val, _ = es.KeyExtractor()(audio)
            dance_val, _  = es.Danceability()(audio)
            loudness_val  = es.Loudness()(audio)
            result.update({
                'es_bpm':         round(float(bpm_val), 1),
                'es_key':         key_val,
                'es_scale':       scale_val,
                'es_danceability':round(float(dance_val), 3),
                'es_loudness':    round(float(loudness_val), 3),
            })
        except Exception as e2:
            print(f"    ⚠️ Essentia 降级分析也失败: {e2}")

    # 可选：Essentia-TensorFlow 情绪模型（占位，需要额外下载模型文件）
    # if HAS_ESSENTIA_TF and result:
    #     mood = predict_essentia_tf_mood(audio_path)
    #     result.update(mood)

    return result


# ============================================================
# 🎵  分析器 3：HuggingFace Genre 分类（仿 auto.py）
# ============================================================
_hf_model_cache = None
_hf_extractor_cache = None

def analyze_hf_genre(audio_path):
    if not (HAS_TORCH and HAS_TRANSFORMERS and HAS_LIBROSA):
        return {}
    global _hf_model_cache, _hf_extractor_cache
    try:
        if _hf_model_cache is None:
            from transformers import AutoModelForAudioClassification, AutoFeatureExtractor
            import torch
            print("  📥 首次加载 HF genre 模型（dima806/music_genres_classification）...")
            model_name = "dima806/music_genres_classification"
            _hf_extractor_cache = AutoFeatureExtractor.from_pretrained(model_name)
            _hf_model_cache = AutoModelForAudioClassification.from_pretrained(model_name)
            _hf_model_cache.eval()
            print("  ✅ HF genre 模型加载完毕")

        import torch
        y, _ = librosa.load(audio_path, sr=16000, duration=30)
        inputs = _hf_extractor_cache(y, sampling_rate=16000, return_tensors="pt", padding=True)
        with torch.no_grad():
            logits = _hf_model_cache(**inputs).logits
        probs = torch.softmax(logits, dim=-1)[0]
        top_idx   = int(probs.argmax())
        top_label = _hf_model_cache.config.id2label[top_idx]
        top_conf  = float(probs[top_idx])

        # 返回 top-3
        top3 = probs.topk(3)
        top3_labels = [_hf_model_cache.config.id2label[int(i)] for i in top3.indices]
        top3_probs  = [round(float(p), 3) for p in top3.values]

        return {
            'hf_genre':      top_label if top_conf >= 0.35 else '',
            'hf_genre_conf': round(top_conf, 3),
            'hf_genre_top3': str(list(zip(top3_labels, top3_probs))),
        }
    except Exception as e:
        print(f"    ⚠️ HF genre 分析失败: {e}")
        return {'hf_genre_error': str(e)}


# ============================================================
# 🎵  分析器 4：LLM (Claude API) 语义标注
# ============================================================
LLM_SYSTEM = """你是专业的短视频背景音乐标注助手，熟悉中文流行音乐。
根据歌名和艺术家（以及可选的音频特征），输出结构化 JSON。
不要输出 markdown 代码块，只输出纯 JSON。

JSON 字段说明：
- music_type: "Vocal" 或 "Instrumental"
- genre: 最匹配的一个流派（流行/电子/民谣/R&B/嘻哈/古风/爵士/摇滚/轻音乐/氛围/其他）
- genre_tags: 最多3个细分标签列表
- emotion_coarse: "Positive" / "Neutral" / "Negative"
- emotion_fine: Energetic/Happy/Romantic/Calm/Melancholic/Intense/Dreamy/Playful/Other
- valence: "high" / "medium" / "low"
- energy_1_5: 1~5 整数（1=极安静，5=极强烈）
- suitable_scene: 最多3个场景（自然风光/城市街头/旅行/运动/美食/宠物/日常生活/情感/夜晚/清晨）
- confidence: "high" / "medium" / "low"
- notes: 简短备注（中文，30字以内）"""

def analyze_llm(music_title, music_author, extra_features=None):
    if llm_client is None:
        return {}

    # 原创音频：没有歌名参考，仅用音频特征标注（需要 extra_features）
    is_original = (not music_title
                   or music_title.startswith('@')
                   or '原声' in music_title)

    if is_original:
        if not extra_features:
            return {'llm_skipped': '原创音频且无音频特征，跳过LLM'}
        user_content = f"原创音频（无歌名），音频特征如下：\n"
    else:
        user_content = f"歌曲：《{music_title}》by {music_author}\n"

    if extra_features:
        bpm = extra_features.get('lr_bpm') or extra_features.get('es_bpm')
        key = extra_features.get('lr_key') or extra_features.get('es_key')
        scale = extra_features.get('es_scale', '')
        dance = extra_features.get('es_danceability') or extra_features.get('lr_dance_score')
        rhythm_str = extra_features.get('lr_rhythm_strength')
        lr_emotion = extra_features.get('lr_emotion_coarse')
        lr_music_type = extra_features.get('lr_music_type')

        feat_parts = []
        if bpm:         feat_parts.append(f"BPM={bpm}")
        if key:         feat_parts.append(f"调性={key}{' ' + scale if scale else ''}")
        if dance:       feat_parts.append(f"律动感={dance:.2f}")
        if rhythm_str:  feat_parts.append(f"节奏强度={rhythm_str:.2f}")
        if lr_emotion:  feat_parts.append(f"librosa情绪={lr_emotion}")
        if lr_music_type: feat_parts.append(f"librosa人声估计={lr_music_type}")
        if feat_parts:
            user_content += "音频特征：" + ", ".join(feat_parts)

    try:
        resp = llm_client.messages.create(
            model=LLM_MODEL,
            max_tokens=600,
            system=LLM_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw).strip()
        raw = re.sub(r'\s*```$', '', raw).strip()
        data = json.loads(raw)

        return {
            'llm_music_type':     data.get('music_type', ''),
            'llm_genre':          data.get('genre', ''),
            'llm_genre_tags':     str(data.get('genre_tags', [])),
            'llm_emotion_coarse': data.get('emotion_coarse', ''),
            'llm_emotion_fine':   data.get('emotion_fine', ''),
            'llm_valence':        data.get('valence', ''),
            'llm_energy_1_5':     data.get('energy_1_5', ''),
            'llm_suitable_scene': str(data.get('suitable_scene', [])),
            'llm_confidence':     data.get('confidence', ''),
            'llm_notes':          data.get('notes', ''),
        }
    except Exception as e:
        print(f"    ⚠️ LLM 标注失败: {e}")
        return {'llm_error': str(e)}


# ============================================================
# 🔄  交叉验证摘要
# ============================================================
def cross_validate(lr, es_res, hf, llm):
    xval = {}

    # BPM 一致性（librosa vs Essentia, ±5 BPM）
    if lr.get('lr_bpm') and es_res.get('es_bpm'):
        diff = abs(lr['lr_bpm'] - es_res['es_bpm'])
        xval['xval_bpm_diff']  = round(diff, 1)
        xval['xval_bpm_agree'] = 'Y' if diff < 5 else 'N'

    # music_type 一致性（librosa vs LLM）
    if lr.get('lr_music_type') and llm.get('llm_music_type'):
        xval['xval_music_type_agree'] = 'Y' if lr['lr_music_type'] == llm['llm_music_type'] else 'N'

    # emotion_coarse 一致性（librosa vs LLM）
    if lr.get('lr_emotion_coarse') and llm.get('llm_emotion_coarse'):
        xval['xval_emotion_agree'] = 'Y' if lr['lr_emotion_coarse'] == llm['llm_emotion_coarse'] else 'N'

    # key 一致性（librosa vs Essentia）
    if lr.get('lr_key') and es_res.get('es_key'):
        xval['xval_key_agree'] = 'Y' if lr['lr_key'] == es_res['es_key'] else 'N'

    return xval


# ============================================================
# 🚀  主流程
# ============================================================
def main():
    os.makedirs(os.path.dirname(OUTPUT_EXCEL), exist_ok=True)

    douk_map = load_douk_meta()
    print(f"📋 DouK 元数据：{len(douk_map)} 条商业歌曲")

    exts = {'.mp3', '.m4a', '.wav', '.flac', '.aac'}
    audio_files = sorted(
        f for f in os.listdir(FULL_MUSIC_DIR)
        if os.path.splitext(f)[1].lower() in exts
    )
    print(f"🎵 找到 {len(audio_files)} 个音频文件\n")

    records = []
    for i, fname in enumerate(audio_files):
        audio_path = os.path.join(FULL_MUSIC_DIR, fname)
        print(f"[{i+1}/{len(audio_files)}] {fname}")

        title, author = parse_stem(fname)
        douk_meta  = douk_map.get(f"{title}_{author}", {})
        music_title  = douk_meta.get('music_title', title)
        music_author = douk_meta.get('music_author', author)
        is_original  = title.startswith('@') or '原声' in title

        row = {
            'filename':     fname,
            'music_title':  music_title,
            'music_author': music_author,
            'is_commercial': 'N' if is_original else 'Y',
        }

        # ---- 各分析器 ----
        print("    → librosa...", end=' ', flush=True)
        lr = analyze_librosa(audio_path)
        print("essentia...", end=' ', flush=True)
        es_res = analyze_essentia(audio_path)
        print("hf-genre...", end=' ', flush=True)
        hf = analyze_hf_genre(audio_path)
        print("llm...", end=' ', flush=True)
        llm = analyze_llm(music_title, music_author, {**lr, **es_res})
        print("done")

        # ---- 交叉验证 ----
        xval = cross_validate(lr, es_res, hf, llm)

        row.update(lr)
        row.update(es_res)
        row.update(hf)
        row.update(llm)
        row.update(xval)
        records.append(row)

        # 简要打印
        parts = []
        if lr.get('lr_bpm'):        parts.append(f"BPM={lr['lr_bpm']}")
        if lr.get('lr_emotion_coarse'): parts.append(f"lr_emotion={lr['lr_emotion_coarse']}")
        if hf.get('hf_genre'):      parts.append(f"hf_genre={hf['hf_genre']}({hf.get('hf_genre_conf','')})")
        if llm.get('llm_genre'):    parts.append(f"llm_genre={llm['llm_genre']}")
        if llm.get('llm_emotion_coarse'): parts.append(f"llm_emotion={llm['llm_emotion_coarse']}")
        print(f"    {' | '.join(parts)}")
        print()

    # ---- 保存 ----
    df = pd.DataFrame(records)

    # 调整列顺序：基础信息 → librosa → Essentia → HF → LLM → 交叉验证
    base_cols  = ['filename','music_title','music_author','is_commercial']
    lr_cols    = [c for c in df.columns if c.startswith('lr_')]
    es_cols    = [c for c in df.columns if c.startswith('es_')]
    hf_cols    = [c for c in df.columns if c.startswith('hf_')]
    llm_cols   = [c for c in df.columns if c.startswith('llm_')]
    xval_cols  = [c for c in df.columns if c.startswith('xval_')]
    ordered    = base_cols + lr_cols + es_cols + hf_cols + llm_cols + xval_cols
    remaining  = [c for c in df.columns if c not in ordered]
    df = df[ordered + remaining]

    df.to_excel(OUTPUT_EXCEL, index=False)
    print(f"\n{'='*60}")
    print(f"✅ 已保存对比结果：{OUTPUT_EXCEL}")
    print(f"   共 {len(df)} 条记录，{len(df.columns)} 列\n")

    # ---- 一致性汇总 ----
    print("📊 交叉验证一致性汇总：")
    for col, label in [
        ('xval_bpm_agree',        'BPM          librosa vs Essentia (±5 BPM)'),
        ('xval_key_agree',        'Key          librosa vs Essentia'),
        ('xval_music_type_agree', 'MusicType    librosa vs LLM'),
        ('xval_emotion_agree',    'EmotionCoarse librosa vs LLM'),
    ]:
        if col in df.columns:
            valid = df[col].notna()
            agree = df.loc[valid, col].eq('Y').sum()
            total = valid.sum()
            print(f"   {label}: {agree}/{total} ({agree/total:.0%})" if total else f"   {label}: 无数据")


if __name__ == "__main__":
    main()
