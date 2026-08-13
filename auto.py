import os
import warnings
import logging

# ==================== 【终极静音配置区：必须在第一行】 ====================
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
warnings.filterwarnings("ignore")
# ==========================================================================

import cv2
import pandas as pd
import librosa
import numpy as np
import tensorflow as tf
from intent_mgsv_pipeline.runtime_config import PATHS
from intent_mgsv_pipeline.preprocessing.prepared_music import (
    load_prepared_music_index,
)

# ==================== 【TensorFlow 专属消音器】 ====================
tf.get_logger().setLevel('ERROR')
logging.getLogger('tensorflow').setLevel(logging.ERROR)

try:
    import absl.logging
    absl.logging.set_verbosity(absl.logging.ERROR)
except ImportError:
    pass
# ==================================================================

from transnetv2 import TransNetV2
from intent_mgsv_pipeline.preprocessing.beatnet_compat import BeatNet
from scipy.signal import find_peaks
from scipy.ndimage import gaussian_filter1d


# ==================== 【全局配置与模型初始化】 ====================

DOUK_DOWNLOAD_ROOT   = str(PATHS.douk_download_root)
DOUK_DATA_EXCEL      = str(PATHS.douk_data_excel)

# 只扫描 DouK-Source 新下载目录
SCAN_ROOTS = [DOUK_DOWNLOAD_ROOT]

OUTPUT_DIR           = str(PATHS.output_dir)
FULL_MUSIC_DIR       = str(PATHS.full_music_dir)
ACR_TRACKING_EXCEL   = str(PATHS.acr_tracking_excel)
SERVER_DB            = PATHS.server_db
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 限制 TF 显存
gpus = tf.config.experimental.list_physical_devices('GPU')
if gpus:
    try:
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)
    except RuntimeError:
        pass

print("正在初始化 BeatNet 和 TransNetV2 模型...")
beatnet_estimator = BeatNet(1, mode='offline', inference_model='DBN', plot=[], thread=False)
transnet_estimator = TransNetV2()


# ==================== 【物理属性提取】 ====================

def get_video_metadata(video_path):
    """提取视频基础物理属性"""
    try:
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        duration = round(total_frames / fps, 3) if fps > 0 else 0
        return duration, width, height, total_frames, round(fps, 2)
    except:
        return 0, 0, 0, 0, 0

def get_audio_metadata(audio_path):
    """提取音频基础物理属性"""
    try:
        y, sr = librosa.load(audio_path, sr=22050)
        return round(librosa.get_duration(y=y, sr=sr), 3)
    except:
        return 0


# ==================== 【核心算法模块】 ====================

def run_audio_alignment(video_path, full_music_path):
    try:
        y_video, sr_video = librosa.load(video_path, sr=22050)
        y_music, sr_music = librosa.load(full_music_path, sr=22050)
        hop_len = 512

        # 检查音频是否有效
        if np.max(np.abs(y_video)) < 1e-4:
            print(f"  ⚠️ 视频音轨信号过弱，跳过对齐")
            return None, None
        if np.max(np.abs(y_music)) < 1e-4:
            print(f"  ⚠️ 音乐文件信号过弱，跳过对齐")
            return None, None

        def _dtw_align(feat_v, feat_m):
            feat_v = np.nan_to_num(feat_v) + 1e-8
            feat_m = np.nan_to_num(feat_m) + 1e-8
            _, wp = librosa.sequence.dtw(X=feat_v, Y=feat_m, metric='cosine', subseq=True)
            s = librosa.frames_to_time(wp[np.argmin(wp[:, 0])][1], sr=sr_music, hop_length=hop_len)
            e = librosa.frames_to_time(wp[np.argmax(wp[:, 0])][1], sr=sr_music, hop_length=hop_len)
            # 合理性校验：对齐区间长度应 ≥ 视频时长的 40%，否则视为对齐失败
            v_duration_sec = len(y_video) / sr_video
            if (e - s) < v_duration_sec * 0.4:
                print(f"  ⚠️ DTW 对齐区间过短（{e-s:.2f}s vs 视频{v_duration_sec:.2f}s），判定为对齐失败")
                return None, None
            return round(float(s), 3), round(float(e), 3)

        # 优先用 Chroma（有旋律的音乐）；纯音乐/环境音 chroma 信号弱则改用 MFCC
        chroma_v = librosa.feature.chroma_stft(y=y_video, sr=sr_video, hop_length=hop_len)
        chroma_m = librosa.feature.chroma_stft(y=y_music, sr=sr_music, hop_length=hop_len)

        if float(np.mean(np.abs(chroma_v))) > 0.01:
            return _dtw_align(chroma_v, chroma_m)

        print(f"  🔄 Chroma 信号弱（纯音乐/环境音），切换为 MFCC 对齐...")
        mfcc_v = librosa.feature.mfcc(y=y_video, sr=sr_video, n_mfcc=20, hop_length=hop_len)
        mfcc_m = librosa.feature.mfcc(y=y_music, sr=sr_music, n_mfcc=20, hop_length=hop_len)
        return _dtw_align(mfcc_v, mfcc_m)

    except Exception as e:
        print(f"  ❌ 音乐片段定位失败: {e}")
        return None, None

def run_beatnet(music_path, m_start, m_end):
    """运行 BeatNet，返回片段内相对节拍列表和 BPM"""
    try:
        output = beatnet_estimator.process(music_path)
        beats = [round(row[0], 3) for row in output]

        # 优先从模型属性读 BPM，若属性不存在则从节拍间隔计算
        try:
            bpm = round(float(beatnet_estimator.bpm), 2)
        except AttributeError:
            if len(beats) >= 2:
                intervals = np.diff(beats)
                median_interval = float(np.median(intervals))
                bpm = round(60.0 / median_interval, 2) if median_interval > 0 else 0.0
            else:
                bpm = 0.0

        relative_beats = []
        if m_start is not None and m_end is not None:
            segment_beats = [b for b in beats if m_start <= b <= m_end]
            relative_beats = [round(float(b - m_start), 3) for b in segment_beats if b >= m_start]

        return relative_beats, bpm
    except Exception as e:
        print(f"  ❌ 音乐节拍检测失败: {e}")
        return [], 0.0

def run_transnetv2(video_path):
    """调用 TransNetV2 提取转场卡点（绝对秒数）"""
    try:
        video_frames, single_preds, all_preds = transnet_estimator.predict_video(video_path)
        scenes = transnet_estimator.predictions_to_scenes(single_preds)
        if len(scenes) <= 1:
            return []
        cut_frames = scenes[1:, 0]
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        if fps <= 0:
            fps = 30.0
        return [round(float(f / fps), 3) for f in cut_frames]
    except Exception as e:
        print(f"  ❌ TransNetV2 转场检测失败: {e}")
        return []

def run_optical_flow(video_path, grid_size=(3, 3)):
    """
    终极光流版 (Optical Flow)：通过追踪像素的"运动速度"而非"颜色面积"，
    完美捕捉流体（喷泉）、连续动作（舞蹈发力点）的真实物理卡点。
    （逻辑来自 pipeline.py run_action_beat_detection）
    """
    try:
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0

        ret, prev_frame = cap.read()
        if not ret:
            return []

        # 【加速核心】：将画面缩小到固定宽度 400，极大降低光流计算量，且不损失动作特征
        scale_ratio = 400.0 / prev_frame.shape[1]
        new_width  = 400
        new_height = int(prev_frame.shape[0] * scale_ratio)

        prev_gray = cv2.cvtColor(cv2.resize(prev_frame, (new_width, new_height)), cv2.COLOR_BGR2GRAY)

        # 计算网格步长
        step_x = new_width  // grid_size[1]
        step_y = new_height // grid_size[0]

        motion_energy = []

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            gray = cv2.cvtColor(cv2.resize(frame, (new_width, new_height)), cv2.COLOR_BGR2GRAY)

            # 计算 Farneback 稠密光流，返回每个像素 (dx, dy) 移动速度和方向的矩阵
            flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None,
                                                0.5, 3, 15, 3, 5, 1.2, 0)

            # 将 (dx, dy) 速度向量转换为绝对速度大小 (Magnitude)
            mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])

            max_grid_energy = 0

            # 网格池化，防止被全屏的微风/镜头晃动干扰
            for row in range(grid_size[0]):
                for col in range(grid_size[1]):
                    grid_mag    = mag[row*step_y:(row+1)*step_y, col*step_x:(col+1)*step_x]
                    grid_energy = np.mean(grid_mag)
                    if grid_energy > max_grid_energy:
                        max_grid_energy = grid_energy

            motion_energy.append(max_grid_energy)
            prev_gray = gray

        cap.release()

        motion_energy = np.array(motion_energy)
        if np.max(motion_energy) == 0:
            return []

        # 动能放大器 (Kinetic Energy Amplifier)：E ∝ v²
        # 缓慢风景平移得分趋近于零，喷泉/舞蹈爆发得分指数级飙升
        motion_energy = motion_energy ** 2
        motion_energy = motion_energy / np.max(motion_energy)

        min_distance_frames = int(fps * 0.3)
        peaks, _ = find_peaks(motion_energy, distance=min_distance_frames, prominence=0.15)

        return [round(float(p / fps), 3) for p in peaks]
    except Exception as e:
        print(f"  ❌ 光流卡点检测失败: {e}")
        return []

# ==================== 【DouK 元数据：读取与匹配】 ====================

def load_douk_metadata():
    """
    读取 DouK-Source 生成的 Download.xlsx，建立两个查找索引：
      id_index:           {作品ID字符串 → metadata_dict}
      time_creator_index: {(date_str "YYYY-MM-DD HH.MM.SS", creator) → metadata_dict}
    metadata_dict 包含：full_desc, hashtags, creator, music_title, music_author, music_url
    """
    import re as _re
    id_index, tc_index = {}, {}
    if not os.path.exists(DOUK_DATA_EXCEL):
        print("📋 未找到 DouK 元数据 Excel，将使用文件名解析")
        return id_index, tc_index
    try:
        df = pd.read_excel(DOUK_DATA_EXCEL)
        for _, row in df.iterrows():
            vid      = str(row.get('作品ID', '')).strip()
            creator  = str(row.get('账号昵称', '')).strip()
            desc     = str(row.get('作品描述', '')).strip()
            topics   = str(row.get('作品话题', '')).strip()   # 已提取，空格分隔
            hashtags = [t.lstrip('#').strip() for t in topics.split() if t]
            title    = _re.sub(r'#\S+', '', desc).strip()
            pub_time = str(row.get('发布时间', '')).strip()   # 格式可能是 "2025-05-13 18:32:41"
            # 统一成文件名里的格式 "YYYY-MM-DD HH.MM.SS"
            date_key = pub_time.replace(':', '.') if pub_time else ''
            meta = {
                'full_desc':    desc,
                'title':        title,
                'hashtags':     hashtags,
                'creator':      creator,
                'music_title':  str(row.get('音乐标题', '')).strip(),
                'music_author': str(row.get('音乐作者', '')).strip(),
                'music_url':    str(row.get('音乐链接', '')).strip(),
                'video_id':     vid,
            }
            if vid:
                id_index[vid] = meta
            if date_key and creator:
                tc_index[(date_key, creator)] = meta
        print(f"📋 已加载 DouK 元数据，共 {len(id_index)} 条记录")
    except Exception as e:
        print(f"  ⚠️ 读取 DouK 元数据失败: {e}")
    return id_index, tc_index


def match_douk_metadata(filename, id_index, tc_index):
    """
    尝试把视频文件名匹配到 DouK 元数据：
      1. 优先：文件名含 18 位数字 ID（新 name_format 含 id 字段）
      2. 兜底：从文件名提取 (日期, 创作者) 做模糊匹配
    返回 metadata_dict 或 None
    """
    import re as _re
    name = os.path.splitext(filename)[0]
    # 1. 精确匹配：18~19 位纯数字 ID
    m = _re.search(r'[^0-9](\d{15,20})[^0-9]', f'-{name}-')
    if m:
        vid = m.group(1)
        if vid in id_index:
            return id_index[vid]
    # 2. 模糊匹配：日期 + 创作者
    date_m = _re.match(r'^(\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})', name)
    creator_m = _re.match(r'^\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}-视频-(.+?)-', name)
    if date_m and creator_m:
        dk = date_m.group(1)      # "2025-05-13 18.32.41"
        cr = creator_m.group(1)   # "陳师傅"
        if (dk, cr) in tc_index:
            return tc_index[(dk, cr)]
    return None


def download_music_from_url(url, output_path):
    """
    直接从 CDN URL 下载音乐文件（DouK 的 音乐链接 字段），
    返回保存路径；已存在则直接复用。
    """
    if not url or url == 'nan':
        return None
    if os.path.exists(output_path):
        return output_path
    try:
        import requests
        resp = requests.get(url, timeout=30, stream=True)
        resp.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        return output_path
    except Exception as e:
        print(f"  ❌ CDN 音乐下载失败: {e}")
        return None


# ======================================================================

def parse_douyin_filename(filename):
    """
    从抖音下载的文件名中解析创作者、标题、Hashtag。
    文件名格式：2026-02-19 17.07.52-视频-创作者名-标题内容 #tag1 #tag2.mp4
    返回 (creator, title, hashtags_list)
    """
    import re
    name = os.path.splitext(filename)[0]
    pattern = r'^\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}-视频-(.+?)-(.+)$'
    match = re.match(pattern, name)
    if not match:
        return '', name, []
    creator  = match.group(1).strip()
    content  = match.group(2)
    hashtags = re.findall(r'#([\w一-鿿·A-Za-z0-9]+)', content)
    title    = re.sub(r'#[\w一-鿿·A-Za-z0-9]+', '', content).strip(' .…·')
    return creator, title, hashtags


def deduplicate_timestamps(timestamps, threshold=0.15):
    """去除时间窗口内的近似重复点，保留每簇中第一个（通常是 TransNetV2 精确转场点）"""
    if not timestamps:
        return []
    result = [timestamps[0]]
    for t in timestamps[1:]:
        if t - result[-1] >= threshold:
            result.append(t)
    return result

def get_rhythm_category(bpm):
    """依据 BPM 自动划分节奏类别（< 60 Slow / 60~120 Medium / 120~180 Fast / > 180 Extreme）"""
    if bpm <= 0:
        return ""
    elif bpm < 60:
        return "Slow"
    elif bpm <= 120:
        return "Medium"
    elif bpm <= 180:
        return "Fast"
    else:
        return "Extreme"


# ==================== 【自动标注扩展：Genre / Emotion / Scene】 ====================
#
# 首次运行前安装依赖：
#   pip install transformers Pillow
#
# 首次运行时会从 HuggingFace 下载模型（Genre ~400MB，CLIP ~600MB），之后本地缓存。
# 任意模块失败时对应字段自动留空，不影响其他流程。

# ---- Genre：GTZAN 10 类 → 用户分类映射 ----
GTZAN_TO_GENRE = {
    'blues':     'RnB',
    'classical': 'Classical',
    'country':   'Folk',
    'disco':     'Electronic',
    'hiphop':    'Rap',
    'jazz':      'RnB',
    'metal':     'Rock',
    'pop':       'Pop',
    'reggae':    'WorldMusic',
    'rock':      'Rock',
}

# ---- Scene：CLIP 零样本分类提示词 ----
SCENE_PROMPTS = {
    "Travel":    "travel vlog outdoor hiking adventure tourism",
    "Food":      "food cooking eating restaurant meal kitchen",
    "Landscape": "beautiful nature landscape scenery mountain river",
    "Sports":    "sports fitness workout exercise gym running",
    "DailyLife": "daily life routine home indoor casual",
    "Study":     "studying reading working at desk library",
    "Night":     "night city lights dark evening neon",
    "Driving":   "driving car road trip highway dashboard",
    "Dating":    "couple dating romantic love park",
    "Cafe":      "cafe coffee shop cozy indoor sitting",
    "CityWalk":  "city walk street urban buildings pedestrian",
    "Other":     "other miscellaneous content",
}

# ---- Scene Atmosphere：(coarse, fine_or_None, rhythm_or_None) → atmosphere ----
ATMOSPHERE_RULES = [
    (('Positive', 'Energetic',    None),      'Energetic'),
    (('Positive', 'Motivational', None),      'Bright'),
    (('Positive', 'Romantic',     None),      'Romantic'),
    (('Positive', None,           'Extreme'), 'Energetic'),
    (('Positive', None,           'Fast'),    'Bright'),
    (('Positive', None,           'Medium'),  'Fresh'),
    (('Neutral',  'Healing',      None),      'Cozy'),
    (('Neutral',  'Calm',         None),      'Quiet'),
    (('Neutral',  None,           'Slow'),    'Relaxing'),
    (('Neutral',  None,           'Medium'),  'Fresh'),
    (('Negative', 'Lonely',       None),      'Emotional'),
    (('Negative', 'Regretful',    None),      'Emotional'),
    (('Negative', None,           None),      'Emotional'),
]

# ---- 懒加载全局句柄 ----
_genre_model     = None
_genre_extractor = None
_clip_model      = None
_clip_processor  = None


def _get_genre_model():
    global _genre_model, _genre_extractor
    if _genre_model is None:
        try:
            import torch  # 必须先 import torch，transformers 5.x 懒加载依赖它
            from transformers import AutoModelForAudioClassification, AutoFeatureExtractor
            print("  📥 首次加载 Genre 分类模型（dima806/music_genres_classification）...")
            model_name       = "dima806/music_genres_classification"
            _genre_extractor = AutoFeatureExtractor.from_pretrained(model_name)
            _genre_model     = AutoModelForAudioClassification.from_pretrained(model_name)
            _genre_model.eval()
            print("  ✅ Genre 模型加载完毕")
        except Exception as e:
            print(f"  ⚠️ Genre 模型加载失败，genre 将留空: {e}")
            _genre_model = "FAILED"
    if _genre_model == "FAILED":
        return None, None
    return _genre_model, _genre_extractor


def _get_clip():
    """使用 open_clip（独立于 transformers），更稳定"""
    global _clip_model, _clip_processor
    if _clip_model is None:
        try:
            import torch
            import open_clip
            print("  📥 首次加载 CLIP 模型（open_clip ViT-B/32，首次需下载约 600MB）...")
            model, _, preprocess = open_clip.create_model_and_transforms(
                'ViT-B-32', pretrained='openai')
            tokenizer = open_clip.get_tokenizer('ViT-B-32')
            model.eval()
            _clip_model     = model
            _clip_processor = {'preprocess': preprocess, 'tokenizer': tokenizer}
            print("  ✅ CLIP 模型加载完毕")
        except Exception as e:
            print(f"  ⚠️ CLIP 模型加载失败，scene_category 将留空: {e}")
            _clip_model = "FAILED"
    if _clip_model == "FAILED":
        return None, None
    return _clip_model, _clip_processor


def run_genre_detection(music_path, confidence_threshold=0.5):
    """直接加载模型推断 Genre，置信度不足时留空"""
    try:
        import torch
        model, extractor = _get_genre_model()
        if model is None:
            return ''
        y, sr = librosa.load(music_path, sr=16000, duration=30)  # 只取前 30s
        inputs = extractor(y, sampling_rate=sr, return_tensors="pt")
        with torch.no_grad():
            logits = model(**inputs).logits
        probs     = torch.softmax(logits, dim=-1)[0]
        top_score = float(probs.max())
        top_idx   = int(probs.argmax())
        if top_score < confidence_threshold:
            return ''
        label = model.config.id2label[top_idx].lower().strip()
        return GTZAN_TO_GENRE.get(label, '')
    except Exception as e:
        print(f"  ⚠️ Genre 检测跳过: {e}")
        return ''


def run_emotion_detection(music_path, bpm):
    """用 librosa 特征规则推断 Emotion Coarse 和（部分）Fine，同时返回节奏强度"""
    try:
        y, sr = librosa.load(music_path, sr=22050, duration=60)

        rms      = float(np.mean(librosa.feature.rms(y=y)))
        centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
        centroid_norm = centroid / (sr / 2.0)

        energy_score = min(rms / 0.08, 1.0)
        tempo_score  = min(bpm / 160.0, 1.0) if bpm > 0 else 0.5
        combined     = energy_score * 0.5 + tempo_score * 0.3 + centroid_norm * 0.2

        if combined >= 0.55:
            coarse = 'Positive'
        elif combined <= 0.35:
            coarse = 'Negative'
        else:
            coarse = 'Neutral'

        fine = ''
        if coarse == 'Positive' and bpm > 120 and energy_score > 0.6:
            fine = 'Energetic'
        elif coarse == 'Neutral' and bpm < 80 and energy_score < 0.4:
            fine = 'Calm'

        # 节奏强度：onset 均值，治愈系/纯音乐通常 < 1.5，节奏强烈的 > 3.0
        onset_env      = librosa.onset.onset_strength(y=y, sr=sr)
        rhythm_strength = float(onset_env.mean())

        return coarse, fine, rhythm_strength
    except Exception as e:
        print(f"  ⚠️ Emotion 检测跳过: {e}")
        return '', '', 1.0


def run_scene_detection_clip(video_path, n_frames=10, confidence_threshold=0.2):
    """用 open_clip 零样本分类推断 Scene Category"""
    try:
        import torch
        from PIL import Image

        model, processor = _get_clip()
        if model is None:
            return ''

        preprocess = processor['preprocess']
        tokenizer  = processor['tokenizer']

        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return ''

        frame_indices = np.linspace(0, total_frames - 1, n_frames, dtype=int)
        frames = []
        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ret, frame = cap.read()
            if ret:
                pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                frames.append(preprocess(pil_img))
        cap.release()

        if not frames:
            return ''

        labels     = list(SCENE_PROMPTS.keys())
        texts      = list(SCENE_PROMPTS.values())
        text_tokens = tokenizer(texts)

        image_tensor = torch.stack(frames)   # (n_frames, C, H, W)

        with torch.no_grad():
            image_feats = model.encode_image(image_tensor)
            text_feats  = model.encode_text(text_tokens)

        image_feats = image_feats / image_feats.norm(dim=-1, keepdim=True)
        text_feats  = text_feats  / text_feats.norm(dim=-1, keepdim=True)

        # 相似度 (n_frames, n_labels) → 跨帧平均 → softmax
        sim      = (image_feats @ text_feats.T).cpu().numpy()
        avg_sim  = sim.mean(axis=0)
        probs    = np.exp(avg_sim) / np.exp(avg_sim).sum()

        top_idx  = int(probs.argmax())
        top_conf = float(probs[top_idx])

        if top_conf < confidence_threshold:
            return ''
        return labels[top_idx]
    except Exception as e:
        print(f"  ⚠️ CLIP 场景检测跳过: {e}")
        return ''


def get_rhythm_strength(music_path):
    """onset 均值，治愈系/纯音乐通常 < 1.5，节奏强烈的 > 3.0，仅供 sync_level 使用"""
    try:
        y, sr = librosa.load(music_path, sr=22050, duration=60)
        return float(librosa.onset.onset_strength(y=y, sr=sr).mean())
    except:
        return 1.0


def compute_sync_level(visual_beats, audio_beats, video_duration, bpm,
                        alignment_threshold=0.1, rhythm_strength=1.0,
                        rhythm_threshold=1.5):
    """
    归一化对齐得分判断 Sync Level。

    问题背景：音频节拍远多于视觉卡点，直接算对齐率会虚高。
    例如 120BPM 节拍间隔 0.5s，随机放一个卡点也有 40% 概率落在 ±0.1s 内。

    做法：
      actual_rate  = 实际对齐的视觉卡点比例
      random_rate  = 给定 BPM 下随机卡点的期望对齐概率
      norm_score   = actual_rate / random_rate  （>1 说明比随机好）

    密度 = 视觉卡点数 / 视频时长 (cuts/sec)

    Ambient   (0): 无卡点，或 norm_score < 1.3（基本等于随机）
    Soft Sync (1): norm_score 1.3~2.0，或密度较低
    Hard Sync (2): norm_score >= 2.0 且密度 >= 0.2 cuts/sec
    """
    if not visual_beats:
        return 0

    density = len(visual_beats) / video_duration if video_duration > 0 else 0

    # 无音频节拍信息时退化为纯密度判断
    if not audio_beats or bpm <= 0:
        if density < 0.1:
            return 0
        elif density < 0.3:
            return 1
        else:
            return 2

    audio_arr   = np.array(sorted(audio_beats))
    aligned     = sum(1 for vb in visual_beats
                      if np.min(np.abs(audio_arr - vb)) < alignment_threshold)
    actual_rate = aligned / len(visual_beats)

    # 随机期望：从实际节拍间隔算，比 60/bpm 更准确（BeatNet 有微小抖动）
    if len(audio_beats) >= 2:
        beat_interval = float(np.median(np.diff(audio_arr)))
    elif bpm > 0:
        beat_interval = 60.0 / bpm
    else:
        beat_interval = 0.5  # 兜底
    random_rate = min(2 * alignment_threshold / beat_interval, 1.0)

    norm_score = actual_rate / random_rate if random_rate > 0 else 0

    if norm_score < 1.3 or density < 0.05:
        result = 0  # Ambient
    elif norm_score >= 2.0 and density >= 0.2:
        result = 2  # Hard Sync
    else:
        result = 1  # Soft Sync

    # 音乐节奏强度不足（治愈系/纯音乐）时，无论对齐率多高都封顶为 Soft Sync
    if rhythm_strength < rhythm_threshold:
        result = min(result, 1)

    return result


def run_vocal_presence(music_path, chunk_sec=4):
    """
    基于视频实际使用的音频片段，时序判断人声存在程度。
    分析对象：视频绑定音频（segment），而非完整原曲（song）。

    原理：把片段切成 chunk_sec 秒小块，对每块算人声频带占比得分；
          根据"有声块"比例三分类：
            None    — 全程无人声（纯音乐 / 前奏）
            Partial — 部分有人声（前奏+首句 / 间奏混排）
            Full    — 持续有人声（主歌/副歌段）

    设计依据：创作者关心的是"这段音乐有没有歌词干扰旁白"，
              而非歌曲在音乐学上的分类。
    """
    try:
        y, sr = librosa.load(music_path, sr=22050)

        chunk_len = chunk_sec * sr
        n_chunks  = max(1, len(y) // chunk_len)
        vocal_flags = []

        for i in range(n_chunks):
            chunk = y[i * chunk_len : (i + 1) * chunk_len]
            if len(chunk) < sr:          # 不足1秒的尾块跳过
                continue

            y_h  = librosa.effects.harmonic(chunk, margin=3.0)
            S    = np.abs(librosa.stft(y_h, n_fft=2048, hop_length=512))
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
            return 'None'       # 几乎全程无人声
        elif vocal_rate >= 0.70:
            return ''           # 不自动填 Full，避免伪完成；交给人工确认
        else:
            return ''           # 不自动填 Partial，避免伪完成；交给人工确认

    except Exception as e:
        print(f"  ⚠️ vocal_presence 检测跳过: {e}")
        return ''


def get_scene_atmosphere(emotion_coarse, emotion_fine, rhythm_cat):
    """规则推断 Scene Atmosphere"""
    for (c, f, r), atmosphere in ATMOSPHERE_RULES:
        if c != emotion_coarse:
            continue
        if f is not None and f != emotion_fine:
            continue
        if r is not None and r != rhythm_cat:
            continue
        return atmosphere
    return ''


# ==================== 【主控引擎：自动扫描与建表】 ====================

def main():
    print(f"正在扫描以下目录:")
    for r in SCAN_ROOTS:
        print(f"  {r}")
    dataset_records = []
    out_excel = os.path.join(OUTPUT_DIR, "MGSV_Master_Dataset.xlsx")

    # =========================================================
    # 🛡️ 增量更新核心逻辑 1：读取历史记录，保护人工标注数据
    # =========================================================
    processed_video_ids = set()
    if os.path.exists(out_excel):
        print(f"📦 检测到历史总表 {out_excel}，正在读取...")
        existing_df = pd.read_excel(out_excel, keep_default_na=False)
        if 'video_id' in existing_df.columns:
            processed_video_ids = set(existing_df['video_id'].dropna().astype(str))
        print(f"✅ 成功锁定 {len(processed_video_ids)} 条已处理的历史记录，将跳过它们的计算！")
    else:
        existing_df = pd.DataFrame()
        print("🌱 未检测到历史表格，将执行全量初始化处理。")

    print("-" * 50)

    prepared_music_index = load_prepared_music_index(
        SERVER_DB,
        owner_id=os.environ.get("MGSV_OWNER_ID", "owner"),
    )
    print(
        f"Music preparation records available: {len(prepared_music_index)}"
    )

    # =========================================================
    # 📋 加载 DouK-Source 元数据（完整描述 + 音乐 CDN 链接）
    # =========================================================
    douk_id_index, douk_tc_index = load_douk_metadata()

    # =========================================================
    # 📀 加载 yt_dy_auto.py 生成的原曲追踪表（若存在）
    # =========================================================
    acr_index = {}
    if os.path.exists(ACR_TRACKING_EXCEL):
        acr_df = pd.read_excel(ACR_TRACKING_EXCEL)
        for _, row in acr_df.iterrows():
            vid = str(row.get('video_id', '')).strip()
            def _acr_text(name):
                value = row.get(name, '')
                return '' if pd.isna(value) else str(value).strip()
            if vid:
                acr_index[vid] = {
                    'full_music_path': _acr_text('full_music_path'),
                    'song_title': _acr_text('song_title'),
                    'song_artist': _acr_text('song_artist'),
                    'acr_confidence': _acr_text('acr_confidence'),
                    'recognition_votes': _acr_text('recognition_votes'),
                    'recognition_samples': _acr_text('recognition_samples'),
                    'recognition_sample_summary': _acr_text('recognition_sample_summary'),
                    'recognition_error': _acr_text('recognition_error'),
                    'recognition_version': _acr_text('recognition_version'),
                }
        print(f"📀 已加载原曲追踪表，共 {len(acr_index)} 条记录")
    else:
        print("📀 未找到原曲追踪表，将使用视频自带音乐进行对齐")

    print("-" * 50)

    # 扫描所有配置目录（去重，避免同一视频被处理两次）
    seen_video_names = set()
    for scan_root in SCAN_ROOTS:
        if not os.path.exists(scan_root):
            print(f"⚠️ 目录不存在，跳过: {scan_root}")
            continue
        print(f"\n📂 扫描: {scan_root}")
        for root, dirs, files in os.walk(scan_root):
            video_files = [f for f in files if f.lower().endswith(('.mp4', '.avi', '.mov'))]
            audio_files = [f for f in files if f.lower().endswith(('.m4a', '.mp3', '.wav'))]

            if not (len(video_files) >= 1 and len(audio_files) >= 1):
                continue

            v_file = video_files[0]
            a_file = audio_files[0]

            # =========================================================
            # 🛡️ 增量更新核心逻辑 2：查字典跳过
            # =========================================================
            if v_file in processed_video_ids or v_file in seen_video_names:
                continue
            seen_video_names.add(v_file)

            video_path = os.path.join(root, v_file)
            music_path = os.path.join(root, a_file)

            print(f"\n🆕 发现新数据！正在处理...")
            print(f"🎬 视频: {v_file}")

            # ---- 优先从 DouK 元数据获取完整 intent 信息 ----
            douk_meta = match_douk_metadata(v_file, douk_id_index, douk_tc_index)
            if douk_meta:
                creator_val  = douk_meta['creator']
                title_val    = douk_meta['title']
                hashtags_val = douk_meta['hashtags']
                print(f"  📋 DouK元数据: {creator_val} | {title_val[:40]} | Tags: {hashtags_val}")
            else:
                creator_val, title_val, hashtags_val = parse_douyin_filename(v_file)
                print(f"  📝 文件名解析: {creator_val or '(未解析)'} | {title_val[:30]} | Tags: {hashtags_val}")

            # ---- 判断是否有完整原曲可用 ----
            # 优先级：ACR追踪表 > DouK 音乐CDN链接 > 视频自带音频
            acr_info = acr_index.get(v_file, {})
            prepared_info = prepared_music_index.get(v_file, {})
            full_music_path = (
                prepared_info.get('full_song_path', '')
                or acr_info.get('full_music_path', '')
            )
            has_full_music  = bool(full_music_path and os.path.exists(full_music_path))

            # 若 ACR 追踪表没有，但 DouK 有 CDN 音乐链接，则直接下载
            if not has_full_music and douk_meta and douk_meta.get('music_url'):
                music_url = douk_meta['music_url']
                # 判断是否为"原创音频"（此类无法在 YouTube/B站找到，直接用 CDN）
                safe_name = f"{douk_meta['music_title']}_{douk_meta['music_author']}".replace('/', '_').replace('\\', '_').replace(' ', '_')[:80]
                cdn_path  = os.path.join(FULL_MUSIC_DIR, f"{safe_name}.mp3")
                if not os.path.exists(cdn_path):
                    print(f"  🌐 从 CDN 下载原声: {douk_meta['music_title']} — {douk_meta['music_author']}")
                    cdn_result = download_music_from_url(music_url, cdn_path)
                else:
                    cdn_result = cdn_path
                if cdn_result:
                    full_music_path = cdn_result
                    has_full_music  = True
                    print(f"  ✅ CDN 音乐已就绪: {os.path.basename(cdn_result)}")

            if has_full_music:
                align_source = full_music_path   # 用完整原曲做 DTW 对齐
                print(f"  📀 使用完整原曲对齐: {os.path.basename(full_music_path)}")
            else:
                align_source = music_path        # 降级：用视频自带音频
                print(f"  🎵 使用自带音频对齐: {a_file}")

            # --- 基础属性 ---
            v_duration, v_width, v_height, v_frames, v_fps = get_video_metadata(video_path)
            m_total_duration = get_audio_metadata(align_source)   # 以对齐源为准
            m_bundled_duration = get_audio_metadata(music_path)   # 自带音频时长（用于 1:1 判断）

            # --- 高阶算法：节奏与视觉 ---
            prepared_offset = prepared_info.get('effective_offset')
            prepared_duration = prepared_info.get('aligned_duration')
            if has_full_music and prepared_offset is not None:
                m_start = round(float(prepared_offset), 3)
                effective_duration = (
                    float(prepared_duration)
                    if prepared_duration is not None
                    else max(
                        0.0,
                        v_duration
                        - float(prepared_info.get('video_audio_start') or 0.0),
                    )
                )
                m_end = round(m_start + effective_duration, 3)
                alignment_source = prepared_info.get(
                    'alignment_source', 'music_preparation'
                )
                print(
                    f"  Using prepared alignment: offset={m_start:.3f}s "
                    f"source={alignment_source}"
                )
            else:
                m_start, m_end = run_audio_alignment(video_path, align_source)
                alignment_source = 'legacy_dtw_fallback'
            beats_rel, bpm_val = run_beatnet(align_source, m_start, m_end)
            rhythm_cat       = get_rhythm_category(bpm_val)
            all_visual_beats = run_transnetv2(video_path)
            music_seg_dur    = round(m_end - m_start, 3) if (m_start is not None and m_end is not None) else None

            # ---- music_grounding_need 判断 ----
            # False  → 视频 BGM 与完整原曲几乎 1:1，无需做 Music Grounding
            # True   → BGM 只是原曲片段，需要 Grounding 定位
            # None   → 无完整原曲，无法判断（仅有自带音频）
            if has_full_music and m_start is not None and m_end is not None and m_total_duration > 0:
                seg_ratio     = (m_end - m_start) / m_total_duration
                is_from_start = m_start < 3.0   # 从原曲头部 3 秒内开始算"从头"
                grounding_need = not (is_from_start and seg_ratio > 0.9)
            else:
                grounding_need = None  # 未知

            # --- 自动标注：仅计算 SyncLevel（其余字段留人工）---
            rhythm_strength_val = get_rhythm_strength(align_source)
            sync_level_val      = compute_sync_level(all_visual_beats, beats_rel, v_duration, bpm_val, rhythm_strength=rhythm_strength_val)
            sync_label = {0: 'Ambient', 1: 'Soft Sync', 2: 'Hard Sync'}.get(sync_level_val, '')
            print(f"  🏷️  Sync={sync_label} | BPM={bpm_val} | visual_beats={all_visual_beats}")

            # =========================================================
            # 列顺序：自动填充列在前，人工标注列在后
            # =========================================================
            record = {
                # —— 自动填充 ——
                'video_id':                  v_file,
                'music_id':                  a_file,
                'song_title':                prepared_info.get('song_title') or acr_info.get('song_title', ''),
                'song_artist':               prepared_info.get('song_artist') or acr_info.get('song_artist', ''),
                # Verification remains owned by the SQLite review transaction.
                'song_verified':             '',
                'acr_confidence':            acr_info.get('acr_confidence', ''),
                'recog_confidence':          acr_info.get('acr_confidence', ''),
                'recognition_votes':         acr_info.get('recognition_votes', ''),
                'recognition_samples':       acr_info.get('recognition_samples', ''),
                'recognition_sample_summary': acr_info.get('recognition_sample_summary', ''),
                'recognition_error':         acr_info.get('recognition_error', ''),
                'recognition_version':       acr_info.get('recognition_version', ''),
                'douyin_video_id':           douk_meta['video_id'] if douk_meta else '',
                'creator_name':              creator_val,
                'video_title':               title_val,
                'hashtags':                  str(hashtags_val),
                'full_desc':                 douk_meta['full_desc'] if douk_meta else '',
                'video_start':               0.0,
                'video_end':                 v_duration,
                'music_start':               m_start,
                'music_end':                 m_end,
                'song_offset':               m_start,
                'song_start':                m_start,
                'song_end':                  m_end,
                'music_grounding_need':      grounding_need,
                'full_music_path':           full_music_path if has_full_music else '',
                'full_song_path':            full_music_path if has_full_music else '',
                'qq_song_mid':               prepared_info.get('qq_song_mid', ''),
                'match_score':               prepared_info.get('match_score'),
                'music_preparation_status':  prepared_info.get('preparation_status', ''),
                'alignment_source':          alignment_source,
                'video_total_duration':      v_duration,
                'video_width':               v_width,
                'video_height':              v_height,
                'video_total_frames':        v_frames,
                'video_frame_rate':          v_fps,
                'music_total_duration':      m_bundled_duration,
                'video_segment_duration':    v_duration,
                'music_segment_duration':    music_seg_dur,
                'bpm':                       bpm_val,
                'rhythm_category':           rhythm_cat,
                'auto_rhythm_points_audio':  str(beats_rel),
                'auto_rhythm_points_visual': str(all_visual_beats),
                'sync_level':                sync_level_val,
                # —— 自动标注 ——
                'vocal_presence':            '',
                # —— 人工标注（基于音乐，从创作者视角） ——
                'emotion':                   '',   # 多选：热血/欢乐/浪漫/伤感/孤独/治愈/放松/平静/紧张/悬疑
                'style':                     '',   # 多选：青春/回忆/高级感/科技感/史诗感/梦幻/国风/二次元/旅行/文艺/卡点/潮流
                'usage_scene':               '',   # 多选：学习/运动/开车/夜晚/散步/旅行/探店/美食/宠物/风景/剧情
            }
            dataset_records.append(record)

    # =========================================================
    # 🛡️ 增量更新核心逻辑 3：无损合并与保存
    # =========================================================
    if len(dataset_records) == 0:
        print("\n☕ 扫描完毕！没有发现新放入的视频。你的数据集已经是最新状态！")
        return

    new_df = pd.DataFrame(dataset_records)

    if not existing_df.empty:
        final_df = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        final_df = new_df

    final_df.to_excel(out_excel, index=False)

    print(f"\n🎉 大功告成！本次新增处理了 {len(dataset_records)} 组新数据！")
    print(f"📊 当前总表总数据量: {len(final_df)} 条。已安全覆盖保存至: {out_excel}")

if __name__ == "__main__":
    main()
