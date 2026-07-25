import os
import json
import pandas as pd
import librosa
import numpy as np
from BeatNet.BeatNet import BeatNet 

import cv2
import tensorflow as tf
from transnetv2 import TransNetV2

import cv2
import numpy as np
from scipy.signal import find_peaks

import cv2
import numpy as np
from scipy.signal import find_peaks

import cv2
import numpy as np
from scipy.signal import find_peaks

def run_action_beat_detection(video_path, grid_size=(3, 3)):
    """
    终极光流版 (Optical Flow)：通过追踪像素的“运动速度”而非“颜色面积”，
    完美捕捉流体（喷泉）、连续动作（舞蹈发力点）的真实物理卡点。
    """
    try:
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0: fps = 30.0
        
        ret, prev_frame = cap.read()
        if not ret: return []
        
        # 【加速核心】：将画面缩小到固定宽度 400，极大降低光流计算量，且不损失动作特征
        scale_ratio = 400.0 / prev_frame.shape[1]
        new_width = 400
        new_height = int(prev_frame.shape[0] * scale_ratio)
        
        prev_gray = cv2.cvtColor(cv2.resize(prev_frame, (new_width, new_height)), cv2.COLOR_BGR2GRAY)
        
        # 计算网格步长
        step_x = new_width // grid_size[1]
        step_y = new_height // grid_size[0]
        
        motion_energy = []
        
        while True:
            ret, frame = cap.read()
            if not ret: break
            
            gray = cv2.cvtColor(cv2.resize(frame, (new_width, new_height)), cv2.COLOR_BGR2GRAY)
            
            # 【黑科技 1】：计算 Farneback 稠密光流
            # 返回的 flow 是一个包含了每个像素 (dx, dy) 移动速度和方向的矩阵
            flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 
                                                0.5, 3, 15, 3, 5, 1.2, 0)
            
            # 【黑科技 2】：将 (dx, dy) 速度向量转换为绝对速度大小 (Magnitude)
            mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            
            max_grid_energy = 0
            
            # 依然保留网格池化，防止被全屏的微风/镜头晃动干扰
            for row in range(grid_size[0]):
                for col in range(grid_size[1]):
                    grid_mag = mag[row*step_y : (row+1)*step_y, col*step_x : (col+1)*step_x]
                    
                    # 取这个格子里像素的“平均运动速度”
                    grid_energy = np.mean(grid_mag)
                    if grid_energy > max_grid_energy:
                        max_grid_energy = grid_energy
                        
            motion_energy.append(max_grid_energy)
            prev_gray = gray
            
        cap.release()
        
        motion_energy = np.array(motion_energy)
        if np.max(motion_energy) == 0:
            return []
            
        # 【黑科技 3】：动能放大器 (Kinetic Energy Amplifier)
        # 物理学公式 E ∝ v^2。将速度平方，可以让缓慢的风景平移得分变得极低，
        # 而喷泉突然爆发（高速度）的得分呈指数级飙升，极大增强波峰！
        motion_energy = motion_energy ** 2
        motion_energy = motion_energy / np.max(motion_energy)
        
        # 寻找波峰
        min_distance_frames = int(fps * 0.3) 
        # 因为平方放大了信号差距，这里的 prominence 可以稍大一点，过滤杂波
        peaks, _ = find_peaks(motion_energy, distance=min_distance_frames, prominence=0.15)
        
        action_timestamps = [round(float(p / fps), 3) for p in peaks]
        
        return action_timestamps
    except Exception as e:
        print(f"  ❌ 光流卡点检测失败: {e}")
        return []

# 【极其重要】限制 TensorFlow 显存按需分配，防止它把 PyTorch (BeatNet) 挤爆
gpus = tf.config.experimental.list_physical_devices('GPU')
if gpus:
    try:
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)
        print("✅ TensorFlow 显存动态分配已开启")
    except RuntimeError as e:
        print(e)

print("正在初始化 BeatNet 深度学习模型...")
beatnet_estimator = BeatNet(1, mode='offline', inference_model='DBN', plot=[], thread=False)

print("正在初始化 TransNetV2 深度学习模型...")
transnet_estimator = TransNetV2()

# ==================== 【用户配置区域】 ====================
EXTERNAL_DATA_ROOT = r"E:/Download/Douyin-Downloader/_internal/Volume/Download"
EXCEL_PATH = r"E:/Desktop/数据标注v1.0.xlsx" 
OUTPUT_DIR = "./outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

print("正在初始化 BeatNet 深度学习模型 (将自动启用 GPU 加速)...")
beatnet_estimator = BeatNet(1, mode='offline', inference_model='DBN', plot=[], thread=False)

def deduplicate_timestamps(timestamps, threshold=0.15):
    """去除时间窗口内的近似重复点，保留每簇中第一个（通常是 TransNetV2 精确转场点）"""
    if not timestamps:
        return []
    result = [timestamps[0]]
    for t in timestamps[1:]:
        if t - result[-1] >= threshold:
            result.append(t)
    return result

def find_file_recursively(root_dir, filename):
    if pd.isna(filename) or not str(filename).strip():
        return None
    filename = str(filename).strip()
    for root, dirs, files in os.walk(root_dir):
        if filename in files:
            return os.path.join(root, filename)
    return None

def run_scene_detection(video_path):
    """调用 TransNetV2 (3D-CNN) 提取视频画面转场点"""
    try:
        # 1. 模型预测：返回每一帧是转场的概率
        # single_frame_predictions 是一个一维数组，值为 0.0 ~ 1.0
        video_frames, single_frame_predictions, all_frame_predictions = transnet_estimator.predict_video(video_path)
        
        # 2. 将概率转化为具体的镜头片段区间 (例如 [[0, 50], [51, 100]])
        scenes = transnet_estimator.predictions_to_scenes(single_frame_predictions)
        
        # 如果视频连一个转场都没有（只有一个长镜头），直接返回空
        if len(scenes) <= 1:
            return []
            
        # 3. 提取每一幕的起始帧作为“切刀点”（跳过第 0 幕的开头）
        cut_frames = scenes[1:, 0]
        
        # 4. 获取视频真实的 FPS，用于将“帧数”精准转化为“秒数”
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        
        if fps <= 0:
            fps = 30.0 # 兜底机制
            
        # 5. 帧数转换为绝对时间（秒）
        cut_timestamps = [round(float(f / fps), 3) for f in cut_frames]
        
        return cut_timestamps
    except Exception as e:
        print(f"  ❌ TransNetV2 视频转场检测失败: {e}")
        return []

def run_audio_alignment(video_path, full_music_path):
    try:
        y_video, sr_video = librosa.load(video_path, sr=22050)
        y_music, sr_music = librosa.load(full_music_path, sr=22050)
        
        hop_len = 512
        chroma_v = librosa.feature.chroma_stft(y=y_video, sr=sr_video, hop_length=hop_len)
        chroma_m = librosa.feature.chroma_stft(y=y_music, sr=sr_music, hop_length=hop_len)
        
        _, wp = librosa.sequence.dtw(X=chroma_v, Y=chroma_m, metric='cosine', subseq=True)
        
        start_frame = wp[np.argmin(wp[:, 0])][1]
        end_frame = wp[np.argmax(wp[:, 0])][1]
        
        music_start = librosa.frames_to_time(start_frame, sr=sr_music, hop_length=hop_len)
        music_end = librosa.frames_to_time(end_frame, sr=sr_music, hop_length=hop_len)
        
        return round(music_start, 3), round(music_end, 3)
    except Exception as e:
        print(f"  ❌ 音乐片段定位失败: {e}")
        return None, None

def run_beat_tracking(music_path):
    try:
        output = beatnet_estimator.process(music_path)
        return [round(row[0], 3) for row in output]
    except Exception as e:
        print(f"  ❌ 音乐节拍检测失败: {e}")
        return []

def main():
    if not os.path.exists(EXCEL_PATH):
        print(f"❌ 找不到标注底表：{EXCEL_PATH}，请检查路径。")
        return
        
    df = pd.read_excel(EXCEL_PATH)
    print(f"成功加载 Excel 底表，共发现 {len(df)} 条待处理数据。")
    print(f"外部数据根目录已指定为: {EXTERNAL_DATA_ROOT}\n" + "="*50)
    
    pred_music_starts, pred_music_ends = [], []
    auto_rhythm_points, video_scenes_list = [], []
    
    for idx, row in df.iterrows():
        v_id = row['video_id']
        m_id = row['music_id']
        
        print(f"[{idx+1}/{len(df)}] 正在多层级检索文件...")
        video_path = find_file_recursively(EXTERNAL_DATA_ROOT, v_id)
        music_path = find_file_recursively(EXTERNAL_DATA_ROOT, m_id)
        
        if not video_path or not music_path:
            print("  ⚠️ 资源未找齐，跳过。")
            pred_music_starts.append(None); pred_music_ends.append(None)
            auto_rhythm_points.append(None); video_scenes_list.append(None)
            continue
            
        print(f"  🎯 成功锁定目标！\n  🎬 视频: {video_path}\n  🎵 音乐: {music_path}")
        
        # TransNetV2 提取物理转场点
        scenes = run_scene_detection(video_path)
        all_visual_beats = scenes
        video_scenes_list.append(json.dumps(all_visual_beats))
        
        m_start, m_end = run_audio_alignment(video_path, music_path)
        pred_music_starts.append(m_start)
        pred_music_ends.append(m_end)
        
        beats = run_beat_tracking(music_path)
        
        if m_start is not None and m_end is not None:
            # 找到落在这个区间内的原曲绝对鼓点
            segment_beats = [b for b in beats if m_start <= b <= m_end]
            
            # 【修改3：核心数学变换】将“原曲绝对时间”重置为短视频播放器视角下的“相对时间”
            relative_beats = [round(float(b - m_start), 3) for b in segment_beats]
            
            # 过滤掉由于四舍五入可能导致的微小负数
            relative_beats = [b for b in relative_beats if b >= 0]
            
            auto_rhythm_points.append(json.dumps(relative_beats))
        else:
            auto_rhythm_points.append(json.dumps([]))
            relative_beats = []
            
        print(f"  ✅ 完成！视觉卡点(合并去重): {all_visual_beats} | 片段内鼓点(相对): {relative_beats}")
        print("-" * 50)
        
    df['auto_music_start'] = pred_music_starts
    df['auto_music_end'] = pred_music_ends
    df['auto_video_scenes'] = video_scenes_list
    df['auto_rhythm_points'] = auto_rhythm_points
    
    out_excel = os.path.join(OUTPUT_DIR, "pre_annotated_metadata.xlsx")
    df.to_excel(out_excel, index=False)
    print(f"\n🎉 高精度自动预标注表已保存至: {out_excel}")

if __name__ == "__main__":
    main()